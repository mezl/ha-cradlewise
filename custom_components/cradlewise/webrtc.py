"""Local WebRTC video receiver — connects directly to the crib's MQTT broker.

Protocol summary (Ant Media Server over local MQTT):
  1. We publish  getOffer  → crib sends back  sendOffer  (SDP offer)
  2. We publish  sendResponse  (our SDP answer) +  iceMsg  (our ICE candidate)
  3. ICE completes; DTLS handshake finishes; H.264 RTP flows.

Two non-obvious requirements discovered by packet analysis:
  - a=setup:passive  — AMS is hardcoded as DTLS client; we must be server.
  - iceMsg trickle ICE — AMS ignores SDP-embedded candidates; they must arrive
    as a separate MQTT message after the answer (same as the phone app).

Certs (mutual TLS to the crib's MQTT broker) must be placed in
  custom_components/cradlewise/certs/
    amazon_root_ca1.pem
    client.pem
    client.key
See certs/README.md for extraction instructions.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import ssl
import time
import uuid
from pathlib import Path
from typing import Callable

import paho.mqtt.client as mqtt
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.sdp import candidate_from_sdp

_LOGGER = logging.getLogger(__name__)

CERTS_DIR = Path(__file__).parent / "certs"
_PORT = 8883
_APP_NAME = "live"


def _get_local_ip_for_host(host: str) -> str:
    """Return the local IP used to route packets to `host`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, 443))
            return s.getsockname()[0]
        except Exception:
            return ""


def _filter_sdp_to_local_ip(sdp: str, local_ip: str) -> tuple[str, int]:
    """Keep only candidates on `local_ip`; rewrite m= port and c= line.

    Returns (filtered_sdp, port).  On failure returns (original_sdp, 0).
    """
    lines = sdp.splitlines()
    wifi_port: int | None = None
    wifi_cands: list[str] = []

    for line in lines:
        if not line.startswith("a=candidate:"):
            continue
        parts = line.split()
        if len(parts) < 8:
            continue
        if parts[4] == local_ip and parts[7] == "host":
            wifi_port = int(parts[5])
            wifi_cands.append(line)
        elif parts[7] == "srflx":
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
    """Receives live H.264 video from the crib via WebRTC-over-local-MQTT."""

    def __init__(
        self,
        host: str,
        cradle_id: str,
        frame_callback: Callable,
    ) -> None:
        self._host = host
        self._cradle_id = cradle_id
        self._room_topic = f"/{cradle_id}/room"
        # Use a stable UUID derived from the cradle_id so each HA instance gets
        # a deterministic, unique client/stream identity without requiring manual
        # configuration.
        self._client_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ha-cradlewise-{cradle_id}"))
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
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)
        client.tls_set_context(ctx)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        client.connect_async(self._host, _PORT, keepalive=15)
        client.loop_start()

    def _publish(self, payload: dict) -> None:
        if self._client:
            self._client.publish(self._room_topic, json.dumps(payload), qos=0)

    def _on_connect(self, client, userdata, flags, rc, props=None) -> None:
        if rc != 0:
            _LOGGER.error("Crib local MQTT connect failed rc=%s", rc)
            return
        _LOGGER.info("Crib local MQTT connected (cradle=%s)", self._cradle_id)
        client.subscribe(self._room_topic, qos=0)
        self._session_id = str(int(time.time() * 1000))
        self._publish({
            "command": "getOffer",
            "direction": "play",
            "streamInfo": {
                "applicationName": _APP_NAME,
                "sessionId": self._session_id,
                "streamName": self._client_id,
            },
            "userData": {"param1": "value1"},
        })

    def _on_disconnect(self, client, userdata, disconnect_flags, rc, props=None) -> None:
        _LOGGER.warning("Crib local MQTT disconnected rc=%s", rc)
        if self._running and self._loop:
            asyncio.run_coroutine_threadsafe(self._reset_pc(), self._loop)

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

    async def _reset_pc(self) -> None:
        if self._pc:
            await self._pc.close()
            self._pc = None

    async def _schedule_reconnect(self) -> None:
        """Re-request a stream offer after a short delay."""
        _LOGGER.info("WebRTC session ended — reconnecting in 8s (cradle=%s)", self._cradle_id)
        await asyncio.sleep(8)
        if not self._running:
            return
        await self._reset_pc()
        self._session_id = str(int(time.time() * 1000))
        self._publish({
            "command": "getOffer",
            "direction": "play",
            "streamInfo": {
                "applicationName": _APP_NAME,
                "sessionId": self._session_id,
                "streamName": self._client_id,
            },
            "userData": {"param1": "value1"},
        })

    async def _handle_offer(self, data: dict) -> None:
        try:
            await self._reset_pc()

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
                _LOGGER.debug("WebRTC %s state: %s", self._cradle_id, state)
                if state in ("failed", "closed") and self._running:
                    asyncio.ensure_future(self._schedule_reconnect())

            await pc.setRemoteDescription(
                RTCSessionDescription(sdp=sdp_data["sdp"], type=sdp_data["type"])
            )

            answer = await pc.createAnswer()

            # AMS is hardcoded as DTLS client (always sends ClientHello).
            # We must be passive (DTLS server) or the handshake fails immediately.
            passive_sdp = answer.sdp.replace("a=setup:active", "a=setup:passive")
            await pc.setLocalDescription(
                RTCSessionDescription(sdp=passive_sdp, type=answer.type)
            )

            # Filter published SDP to only the interface that can reach the crib.
            local_ip = _get_local_ip_for_host(self._host)
            full_sdp = pc.localDescription.sdp
            filtered_sdp, local_port = _filter_sdp_to_local_ip(full_sdp, local_ip)
            if not local_port:
                filtered_sdp = full_sdp

            self._publish({
                "command": "sendResponse",
                "direction": "play",
                "sdp": {"sdp": filtered_sdp, "type": pc.localDescription.type},
                "streamInfo": {
                    "applicationName": _APP_NAME,
                    "sessionId": self._session_id,
                    "streamName": self._client_id,
                },
                "userData": {"param1": "value1"},
            })

            # AMS ignores SDP-embedded candidates; send ours via trickle ICE.
            if local_port and local_ip:
                for line in full_sdp.splitlines():
                    if not line.startswith("a=candidate:"):
                        continue
                    parts = line.split()
                    if len(parts) >= 8 and parts[4] == local_ip and parts[7] == "host":
                        self._publish({
                            "iceMsg": {
                                "candidate": line[2:],  # strip leading "a="
                                "sdpMLineIndex": 0,
                                "sdpMid": "video0",
                            },
                            "streamInfo": {
                                "applicationName": _APP_NAME,
                                "sessionId": self._session_id,
                                "streamName": self._client_id,
                            },
                        })
                        await asyncio.sleep(0.1)
                        self._publish({
                            "iceMsg": {
                                "candidate": "",  # end-of-candidates
                                "sdpMLineIndex": 0,
                                "sdpMid": "video0",
                            },
                            "streamInfo": {
                                "applicationName": _APP_NAME,
                                "sessionId": self._session_id,
                                "streamName": self._client_id,
                            },
                        })
                        break

            asyncio.ensure_future(self._keepalive_loop())

        except Exception:
            _LOGGER.exception("WebRTC offer handling failed for %s", self._cradle_id)

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
            _LOGGER.debug("ICE candidate error: %s", e)

    async def _receive_video(self, track) -> None:
        _LOGGER.info("H.264 video stream started (cradle=%s)", self._cradle_id)
        while True:
            try:
                frame = await track.recv()
                self._frame_callback(frame)
            except Exception as e:
                _LOGGER.debug("Video stream ended (cradle=%s): %s", self._cradle_id, e)
                break

    async def _keepalive_loop(self) -> None:
        while self._pc and self._pc.connectionState not in ("closed", "failed"):
            self._publish({
                "direction": "play",
                "command": "keepAlive",
                "streamInfo": {
                    "applicationName": _APP_NAME,
                    "sessionId": self._session_id,
                    "streamName": self._client_id,
                },
                "userData": {"param1": "value1"},
            })
            await asyncio.sleep(5)
