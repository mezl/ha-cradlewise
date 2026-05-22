"""Cradlewise WebRTC video receiver — connects to crib's local MQTT/WebRTC."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import ssl
import time
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

_LOGGER = logging.getLogger(__name__)

CERTS_DIR = Path(__file__).parent / "certs"
_HOST = "192.168.14.233"
_PORT = 8883
_CRADLE_ID = "29976d6e-4bbf-433c-bdf6-0ed6606e3fc5"
_ROOM_TOPIC = f"/{_CRADLE_ID}/room"
_APP_NAME = "live"

# The crib's local MQTT broker whitelists this specific device ID.
# The reconnect logic in _schedule_reconnect handles recovery when the phone
# app temporarily takes over the stream.
_CLIENT_ID = "05f8aa8c-7dce-4e3e-9063-5f9f1046956f"
_STREAM_NAME = _CLIENT_ID

# How long to wait before re-requesting a stream after it drops.
_RECONNECT_DELAY = 8.0


def _get_local_ip_for_host(host: str) -> str:
    """Return the local IP address used to route packets to `host`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, 443))
            return s.getsockname()[0]
        except Exception:
            return ""


def _filter_sdp_wifi_only(sdp: str, local_ip: str) -> tuple[str, int]:
    """Keep only candidates reachable on the same subnet as the crib."""
    lines = sdp.splitlines()
    wifi_port: int | None = None
    wifi_cands: list[str] = []

    for line in lines:
        if not line.startswith("a=candidate:"):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        cand_ip = parts[4]
        cand_type = parts[7]
        if cand_ip == local_ip and cand_type == "host":
            wifi_port = int(parts[5])
            wifi_cands.append(line)
        elif cand_type == "srflx":
            m = re.search(r"raddr (\S+)", line)
            if m and m.group(1) == local_ip:
                wifi_cands.append(line)

    if not wifi_port:
        _LOGGER.warning("filter_sdp: no candidate for %s — using unfiltered SDP", local_ip)
        return sdp, 0

    new_lines: list[str] = []
    cands_added = False
    for line in lines:
        if line.startswith("m="):
            parts = line.split()
            parts[1] = str(wifi_port)
            new_lines.append(" ".join(parts))
            cands_added = False
        elif line.startswith("c=IN IP4"):
            new_lines.append(f"c=IN IP4 {local_ip}")
        elif line.startswith("a=candidate:"):
            pass
        elif line.startswith("a=end-of-candidates"):
            if not cands_added:
                new_lines.extend(wifi_cands)
                cands_added = True
            new_lines.append(line)
        else:
            new_lines.append(line)
    return "\n".join(new_lines), wifi_port


def _make_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context(
        ssl.Purpose.SERVER_AUTH,
        cafile=str(CERTS_DIR / "amazon_root_ca1.pem"),
    )
    ctx.load_cert_chain(
        certfile=str(CERTS_DIR / "client.pem"),
        keyfile=str(CERTS_DIR / "client.key"),
    )
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class CradlewiseVideoReceiver:
    """Receives H264 video from the crib via WebRTC-over-MQTT."""

    def __init__(self, frame_callback: Callable) -> None:
        self._frame_callback = frame_callback
        self._client: mqtt.Client | None = None
        self._pc: RTCPeerConnection | None = None
        self._session_id: str = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._running = True
        self._connect_mqtt()

    def stop(self) -> None:
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        if self._pc and self._loop:
            asyncio.run_coroutine_threadsafe(self._pc.close(), self._loop)
            self._pc = None

    # ── MQTT ────────────────────────────────────────────────────────────────

    def _connect_mqtt(self) -> None:
        ctx = _make_ssl_ctx()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=_CLIENT_ID)
        client.tls_set_context(ctx)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        client.connect_async(_HOST, _PORT, keepalive=15)
        client.loop_start()

    def _publish(self, payload: dict) -> None:
        if self._client:
            self._client.publish(_ROOM_TOPIC, json.dumps(payload), qos=0)

    def _request_offer(self) -> None:
        self._session_id = str(int(time.time() * 1000))
        self._publish({
            "command": "getOffer",
            "direction": "play",
            "streamInfo": {
                "applicationName": _APP_NAME,
                "sessionId": self._session_id,
                "streamName": _STREAM_NAME,
            },
            "userData": {"param1": "value1"},
        })
        _LOGGER.debug("Sent getOffer (session=%s)", self._session_id)

    def _on_connect(self, client, userdata, flags, rc, props=None) -> None:
        if rc != 0:
            _LOGGER.error("Local MQTT connect failed rc=%s", rc)
            return
        _LOGGER.info("Cradlewise local MQTT connected")
        client.subscribe(_ROOM_TOPIC, qos=0)
        self._request_offer()

    def _on_disconnect(self, client, userdata, disconnect_flags, rc, props=None) -> None:
        _LOGGER.warning("Local MQTT disconnected rc=%s", rc)
        # paho-mqtt auto-reconnects; on_connect will re-send getOffer.
        if self._running and self._loop:
            asyncio.run_coroutine_threadsafe(self._reset_peer_connection(), self._loop)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            data = json.loads(msg.payload)
        except Exception:
            return
        cmd = data.get("command", "")
        if cmd == "sendOffer" and self._loop:
            asyncio.run_coroutine_threadsafe(self._handle_offer(data), self._loop)
        elif "ice" in data and self._loop:
            asyncio.run_coroutine_threadsafe(self._handle_remote_ice(data), self._loop)

    # ── WebRTC ───────────────────────────────────────────────────────────────

    async def _reset_peer_connection(self) -> None:
        if self._pc:
            await self._pc.close()
            self._pc = None

    async def _schedule_reconnect(self) -> None:
        """Wait, then re-request a stream offer from AMS."""
        _LOGGER.info("WebRTC session ended — reconnecting in %.0fs", _RECONNECT_DELAY)
        await asyncio.sleep(_RECONNECT_DELAY)
        if not self._running:
            return
        await self._reset_peer_connection()
        self._request_offer()

    async def _handle_offer(self, data: dict) -> None:
        try:
            await self._reset_peer_connection()

            sdp_data = data.get("sdp", {})
            stream_info = data.get("streamInfo", {})
            self._session_id = stream_info.get("sessionId", self._session_id)

            pc = RTCPeerConnection()
            self._pc = pc

            @pc.on("track")
            def on_track(track):
                if track.kind == "video":
                    asyncio.ensure_future(self._receive_video(track))

            @pc.on("connectionstatechange")
            async def on_state():
                state = pc.connectionState
                _LOGGER.debug("WebRTC state: %s", state)
                if state in ("failed", "closed") and self._running:
                    asyncio.ensure_future(self._schedule_reconnect())

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp_data["sdp"], type=sdp_data["type"])
            )

            answer = await pc.createAnswer()

            # AMS is hardcoded as DTLS client; we must be passive (server).
            passive_sdp = answer.sdp.replace("a=setup:active", "a=setup:passive")
            await pc.setLocalDescription(
                RTCSessionDescription(sdp=passive_sdp, type=answer.type)
            )

            local_ip = _get_local_ip_for_host(_HOST)
            full_sdp = pc.localDescription.sdp
            filtered_sdp, wifi_port = _filter_sdp_wifi_only(full_sdp, local_ip)
            if not wifi_port:
                filtered_sdp = full_sdp

            self._publish({
                "command": "sendResponse",
                "direction": "play",
                "sdp": {"sdp": filtered_sdp, "type": pc.localDescription.type},
                "streamInfo": {
                    "applicationName": _APP_NAME,
                    "sessionId": self._session_id,
                    "streamName": _STREAM_NAME,
                },
                "userData": {"param1": "value1"},
            })

            # AMS ignores SDP-embedded candidates — send via trickle iceMsg.
            if wifi_port and local_ip:
                for line in full_sdp.splitlines():
                    if not line.startswith("a=candidate:"):
                        continue
                    parts = line.split()
                    if len(parts) >= 8 and parts[4] == local_ip and parts[7] == "host":
                        self._publish({
                            "iceMsg": {
                                "candidate": line[2:],
                                "sdpMLineIndex": 0,
                                "sdpMid": "video0",
                            },
                            "streamInfo": {
                                "applicationName": _APP_NAME,
                                "sessionId": self._session_id,
                                "streamName": _STREAM_NAME,
                            },
                        })
                        await asyncio.sleep(0.1)
                        self._publish({
                            "iceMsg": {
                                "candidate": "",
                                "sdpMLineIndex": 0,
                                "sdpMid": "video0",
                            },
                            "streamInfo": {
                                "applicationName": _APP_NAME,
                                "sessionId": self._session_id,
                                "streamName": _STREAM_NAME,
                            },
                        })
                        break

            asyncio.ensure_future(self._keepalive_loop())

        except Exception:
            _LOGGER.exception("_handle_offer failed")

    async def _handle_remote_ice(self, data: dict) -> None:
        if not self._pc:
            return
        ice = data.get("ice", {})
        candidate_str = ice.get("candidate", "")
        if not candidate_str:
            return
        try:
            c = candidate_from_sdp(candidate_str)
            c.sdpMLineIndex = ice.get("sdpMLineIndex", 0)
            c.sdpMid = ice.get("sdpMid") or "video0"
            await self._pc.addIceCandidate(c)
        except Exception as e:
            _LOGGER.debug("ICE candidate error (non-fatal): %s", e)

    async def _receive_video(self, track) -> None:
        _LOGGER.info("H264 video stream started")
        while True:
            try:
                frame = await track.recv()
                self._frame_callback(frame)
            except Exception as e:
                _LOGGER.debug("Video stream ended: %s", e)
                break

    async def _keepalive_loop(self) -> None:
        while self._pc and self._pc.connectionState not in ("closed", "failed"):
            self._publish({
                "direction": "play",
                "command": "keepAlive",
                "streamInfo": {
                    "applicationName": _APP_NAME,
                    "sessionId": self._session_id,
                    "streamName": _STREAM_NAME,
                },
                "userData": {"param1": "value1"},
            })
            await asyncio.sleep(5)
