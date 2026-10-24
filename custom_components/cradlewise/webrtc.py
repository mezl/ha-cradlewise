"""Cradlewise WebRTC video receiver — crib LOCAL broker, firmware 5.80+ recipe.

Rewritten 2026-10-24 after crib firmware 5.80 (updated 2026-10-22) changed AMS
behavior. The old recipe (port-rewrite filter, SDP-embedded candidates,
empty-candidate marker, 5s STUN gather) now hangs: AMS expires the pending
session in ~1-2s, so the answer must go out in <100ms.

Verified working 2026-10-24 01:31 & 01:40 (video frames flowing):
1. NO STUN server: RTCPeerConnection(RTCConfiguration(iceServers=[])) — aiortc's
   default Google-STUN srflx query burns a 5s gather timeout and AMS is gone by
   then. Offer→sendResponse lands in ~15ms.
2. Passive DTLS (unchanged from v1): AMS is a hardcoded DTLS client; patch
   a=setup:active → a=setup:passive BEFORE setLocalDescription. (Answering
   "active" like the app does gets ICE up but DTLS dies with an empty error
   for non-app clients.)
3. App-shaped answer SDP: strip ALL a=candidate: lines and a=end-of-candidates,
   m-line ports → 9, keep c=IN IP4 0.0.0.0, single sha-256 fingerprint. Then
   trickle ONLY the LAN host candidate via iceMsg (sdpMid "video0") — NO
   empty-candidate marker.

Two-way talk (CribTalkTrack) is NOT wired in this version: the 5.80 offer's
audio m-line is bundle-only port-0 and only the recvonly answer shape is
verified against the new AMS. Re-add after testing.

Reference capture of a real app session: /tmp/app_session_capture.json (itx).
Proof-of-concept holder: /tmp/ams_hold.py (itx, 2026-10-24).
"""
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

import av
import numpy as np
import paho.mqtt.client as mqtt
from aiortc import (
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.sdp import candidate_from_sdp

_LOGGER = logging.getLogger(__name__)

CERTS_DIR = Path(__file__).parent / "certs"
_HOST = "192.168.14.231"
_PORT = 8883
_CRADLE_ID = "29976d6e-4bbf-433c-bdf6-0ed6606e3fc5"
_ROOM_TOPIC = f"/{_CRADLE_ID}/room"
_APP_NAME = "live"

# Seed-row identity (phone-tier cert, unused by any real client, so HA never
# fights the app over the one-session-per-clientId rule). Certs for this id
# must be installed as certs/client.pem + certs/client.key.
# Stream name is the shared phone row the crib already streams to.
_CLIENT_ID = "0c3c101d-29e5-4c45-9c90-843701c3362a"
_STREAM_NAME = "dc281e34-2aa0-4ccd-92ea-2b35021cdf5e"

_RECONNECT_DELAY = 8.0


def _get_local_ip_for_host(host: str) -> str:
    """Return the local IP address used to route packets to `host`."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((host, 443))
            return s.getsockname()[0]
        except Exception:
            return ""


def _strip_candidates_app_shape(sdp: str) -> str:
    """App-shaped answer: no candidates, m-line ports 9 (candidates trickle)."""
    out_lines: list[str] = []
    for line in sdp.splitlines():
        if line.startswith("m="):
            parts = line.split()
            parts[1] = "9"
            out_lines.append(" ".join(parts))
        elif line.startswith(("a=candidate:", "a=end-of-candidates")):
            continue
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


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
    """Receives H264 video (and audio if offered) from the crib via WebRTC-over-MQTT."""

    def __init__(self, frame_callback: Callable, audio_callback: Callable | None = None) -> None:
        self._frame_callback = frame_callback
        self._audio_callback = audio_callback
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

    def push_audio(self, pcm: bytes) -> None:
        """Accept raw s16le stereo 48 kHz PCM for the crib speaker.

        Two-way talk is disabled in this version: the 5.80 offer's audio
        m-line is bundle-only port 0 and only the recvonly answer shape is
        verified against the new AMS. Drop the PCM (no-op) until the talk
        track is re-verified — matches v1's behavior when no talk track
        was active. See module docstring.
        """
        _LOGGER.debug("push_audio dropped (talk disabled in 5.80 receiver)")

    # ── MQTT ────────────────────────────────────────────────────────────────

    def _connect_mqtt(self) -> None:
        ctx = _make_ssl_ctx()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=_CLIENT_ID)
        client.tls_set_context(ctx)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        client.connect_async(_HOST, _PORT, keepalive=60)
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

            # NO STUN: default Google-STUN costs a 5s gather; AMS is long gone.
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            self._pc = pc

            @pc.on("track")
            def on_track(track):
                _LOGGER.info("Received %s track from crib", track.kind)
                if track.kind == "video":
                    asyncio.ensure_future(self._receive_video(track))
                elif track.kind == "audio" and self._audio_callback:
                    asyncio.ensure_future(self._receive_audio(track))

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
            # AMS is a hardcoded DTLS client → we must answer passive (server).
            answer = RTCSessionDescription(
                sdp=answer.sdp.replace("a=setup:active", "a=setup:passive"),
                type=answer.type,
            )
            # App shape: candidates stripped, m-ports 9; they trickle instead.
            await pc.setLocalDescription(
                RTCSessionDescription(
                    sdp=_strip_candidates_app_shape(answer.sdp), type=answer.type
                )
            )

            local_ip = _get_local_ip_for_host(_HOST)
            self._publish({
                "command": "sendResponse",
                "direction": "play",
                "sdp": {
                    "sdp": _strip_candidates_app_shape(pc.localDescription.sdp),
                    "type": pc.localDescription.type,
                },
                "streamInfo": {
                    "applicationName": _APP_NAME,
                    "sessionId": self._session_id,
                    "streamName": _STREAM_NAME,
                },
                "userData": {"param1": "value1"},
            })
            _LOGGER.debug("Sent sendResponse (session=%s)", self._session_id)

            # Trickle ONLY the routable LAN host candidate, like the app.
            # No empty-candidate marker in the 5.80 flow.
            if local_ip:
                for line in pc.localDescription.sdp.splitlines():
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

    async def _receive_audio(self, track) -> None:
        _LOGGER.info("Audio stream started")
        first = True
        while True:
            try:
                frame = await track.recv()
                if first:
                    _LOGGER.info(
                        "Audio frame: format=%s rate=%s channels=%s samples=%s",
                        frame.format.name, frame.sample_rate,
                        len(frame.layout.channels), frame.samples,
                    )
                    first = False
                self._audio_callback(frame)
            except Exception as e:
                _LOGGER.debug("Audio stream ended: %s", e)
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