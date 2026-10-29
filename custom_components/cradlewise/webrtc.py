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
from aiortc.jitterbuffer import JitterBuffer
from aiortc.sdp import candidate_from_sdp
from aiortc.utils import uint16_add

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
# Watchdog: no decoded video frame for this long (and no offer request in that
# window either) → tear down and request a fresh session. Backs off on repeated
# stalls so an unresponsive crib is not hammered.
_STALL_TIMEOUT = 20.0
_STALL_TIMEOUT_MAX = 120.0
_WATCHDOG_INTERVAL = 5.0


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


_RCVBUF = 4 * 1024 * 1024  # host net.core.rmem_max on HAOS


def _grow_rcvbuf(pc: RTCPeerConnection) -> None:
    """Enlarge the ICE UDP socket buffers (default 212 KB).

    The receiver runs on HA's busy event loop; a keyframe burst arriving while
    the loop is briefly blocked overflowed the default buffer (host
    RcvbufErrors rose together with crib packet holes)."""
    seen = set()
    for t in pc.getTransceivers():
        try:
            conn = t.receiver.transport.transport.iceGatherer._connection
        except AttributeError:
            continue
        for proto in getattr(conn, "_protocols", []):
            sock = proto.transport.get_extra_info("socket") if proto.transport else None
            if sock is None or sock.fileno() in seen:
                continue
            seen.add(sock.fileno())
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, _RCVBUF)
                _LOGGER.debug("ICE socket rcvbuf now %d", sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
            except OSError as e:
                _LOGGER.debug("rcvbuf resize failed: %s", e)


def _udp_rcvbuf_errors() -> int:
    """Host-wide UDP datagrams dropped because a socket buffer was full."""
    try:
        with open("/proc/net/snmp") as f:
            rows = [l.split() for l in f if l.startswith("Udp:")]
        return int(rows[1][rows[0].index("RcvbufErrors")])
    except Exception:
        return 0


class _VideoStats:
    """Per-minute RTP/frame counters, logged as one INFO line.

    Tells network loss apart from receiver stalls: aiortc releases frames in
    strict sequence order, so one lost packet holds every later frame until it
    is resent (NACK) or the jitter buffer overflows (then a PLI is sent)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.t0 = time.monotonic()
        self.pkts = 0
        self.missing = 0     # sequence holes seen (packets not yet received)
        self.late = 0        # older packets arriving later (resends / reordering)
        self.pkt_gap = 0.0   # longest silence between two packets
        self.last_pkt = 0.0
        self.hi: int | None = None
        self.nacks = 0
        self.plis = 0
        self.frames = 0
        self.keyframes = 0
        self.frame_gap = 0.0
        self.gaps_1s = 0
        self.last_frame = 0.0
        self.cb_ms = 0.0     # worst frame-callback time (blocks the HA loop)
        self.skips = 0       # holes given up on (fast-skip jitter buffer)
        self.rcvbuf0 = _udp_rcvbuf_errors()

    def on_packet(self, seq: int) -> None:
        now = time.monotonic()
        if self.last_pkt:
            self.pkt_gap = max(self.pkt_gap, now - self.last_pkt)
        self.last_pkt = now
        self.pkts += 1
        if self.hi is None:
            self.hi = seq
            return
        d = (seq - self.hi) & 0xFFFF
        if 0 < d < 0x8000:
            self.missing += d - 1
            self.hi = seq
        elif d >= 0x8000:
            self.late += 1

    def on_frame(self, key: bool, cb_s: float) -> None:
        now = time.monotonic()
        if self.last_frame:
            g = now - self.last_frame
            self.frame_gap = max(self.frame_gap, g)
            self.gaps_1s += g > 1.0
        self.last_frame = now
        self.frames += 1
        self.keyframes += key
        self.cb_ms = max(self.cb_ms, cb_s * 1000)

    def line(self) -> str:
        dt = max(time.monotonic() - self.t0, 1e-6)
        return (
            f"{self.frames / dt:.1f} fps, {self.keyframes} keyframes, gaps>1s {self.gaps_1s}, "
            f"max frame gap {self.frame_gap * 1000:.0f} ms | {self.pkts} pkts, "
            f"holes {self.missing}, late {self.late}, max pkt gap {self.pkt_gap * 1000:.0f} ms, "
            f"nack {self.nacks}, pli {self.plis}, skips {self.skips}, host UDP rcvbuf drops "
            f"{_udp_rcvbuf_errors() - self.rcvbuf0} | frame cb max {self.cb_ms:.0f} ms"
        )


_HOLE_GRACE = 0.05  # s to wait for a missing packet before giving up on it
_PLI_EVERY = 0.5    # s between keyframe requests while waiting for one
_START = b"\x00\x00\x00\x01"


def _nal_types(data: bytes) -> set[int]:
    """NAL unit types in an Annex-B blob (aiortc always writes 4-byte start codes)."""
    types, i = set(), data.find(_START)
    while 0 <= i < len(data) - 4:
        types.add(data[i + 4] & 0x1F)
        i = data.find(_START, i + 4)
    return types


class _FastSkipJitterBuffer(JitterBuffer):
    """Video jitter buffer that gives up on lost packets quickly.

    The crib ignores NACKs (no packet is ever resent), but aiortc's buffer holds
    every later frame until the 128-packet ring overflows (~1.2 s at this
    bitrate), then flushes a burst of frames. Here the broken frame is dropped
    after _HOLE_GRACE and the frames behind it are released right away, with a
    (rate-limited) keyframe request. Gating on a clean keyframe was tried in
    simulation and is worse: keyframes are ~5x bigger, so under loss they break
    too and video could freeze for 10+ s."""

    def __init__(self, stats: "_VideoStats") -> None:
        super().__init__(capacity=128, is_video=True)
        self._stats = stats
        self._hole_t: float | None = None
        self._last_pli = 0.0

    def _slot(self, seq: int):
        return self._packets[seq % self._capacity]

    def _stalled(self, packet) -> bool:
        """The oldest frame is incomplete although a newer frame has started."""
        head = self._slot(self._origin)
        return head is None or head.timestamp != packet.timestamp

    def _skip_broken_frame(self, newest: int) -> None:
        end = uint16_add(newest, 1)
        dropped_lead = False
        while self._origin != end and self._slot(self._origin) is None:
            self.remove(1)
            dropped_lead = True
        if self._origin == end:
            return
        first = self._slot(self._origin)
        data = getattr(first, "_data", b"")
        if dropped_lead and data.startswith(_START) and len(data) > 4 and data[4] & 0x1F in (7, 9):
            return  # the hole ended exactly before a new access unit
        ts = first.timestamp
        while self._origin != end:
            pkt = self._slot(self._origin)
            if pkt is not None and pkt.timestamp != ts:
                break
            self.remove(1)

    def add(self, packet):
        pli, frame = super().add(packet)
        now = time.monotonic()
        if frame is not None or self._origin is None or not self._stalled(packet):
            self._hole_t = None
        elif self._hole_t is None:
            self._hole_t = now
        elif now - self._hole_t >= _HOLE_GRACE:
            self._skip_broken_frame(packet.sequence_number)
            self._hole_t = None
            self._stats.skips += 1
            frame = self._remove_frame(packet.sequence_number)
            # Ask for a fresh keyframe so the decoder stops smearing soon.
            pli = pli or now - self._last_pli >= _PLI_EVERY
        if pli:
            self._last_pli = now
        return pli, frame


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
        self._reconnect_pending = False
        self._last_frame = 0.0      # monotonic time of the last decoded video frame
        self._last_request = 0.0    # monotonic time of the last getOffer
        self._stalls = 0            # consecutive watchdog recoveries (backoff)
        self._watchdog = None
        self._vstats = _VideoStats()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._running = True
        self._last_request = time.monotonic()
        self._connect_mqtt()
        self._watchdog = asyncio.run_coroutine_threadsafe(self._watchdog_loop(), loop)

    def stop(self) -> None:
        self._running = False
        if self._watchdog:
            self._watchdog.cancel()
            self._watchdog = None
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
        self._last_request = time.monotonic()
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
        # Detach BEFORE closing: the old pc's "closed" event must see that it is
        # no longer current, or it schedules a reconnect that kills the NEXT
        # session (the old self-sustaining 8 s reconnect loop).
        pc, self._pc = self._pc, None
        if pc:
            await pc.close()

    async def _schedule_reconnect(self) -> None:
        if self._reconnect_pending:
            return
        self._reconnect_pending = True
        try:
            _LOGGER.info("WebRTC session ended — reconnecting in %.0fs", _RECONNECT_DELAY)
            await asyncio.sleep(_RECONNECT_DELAY)
            if not self._running:
                return
            await self._reset_peer_connection()
            self._request_offer()
        finally:
            self._reconnect_pending = False

    async def _watchdog_loop(self) -> None:
        """Recover from silent stalls: offer never answered, or frames stopped
        without the connection reporting failed (both seen in the field)."""
        while self._running:
            await asyncio.sleep(_WATCHDOG_INTERVAL)
            if self._last_frame and time.monotonic() - self._vstats.t0 >= 60:
                _LOGGER.info("Crib video: %s", self._vstats.line())
                self._vstats.reset()
            if not self._running or self._reconnect_pending:
                continue
            limit = min(_STALL_TIMEOUT * (2 ** self._stalls), _STALL_TIMEOUT_MAX)
            now = time.monotonic()
            if now - max(self._last_frame, self._last_request) < limit:
                continue
            self._stalls += 1
            _LOGGER.warning(
                "No crib video for %.0fs — requesting a new WebRTC session (stall #%d)",
                now - self._last_frame if self._last_frame else -1, self._stalls,
            )
            try:
                await self._reset_peer_connection()
            except Exception:
                _LOGGER.debug("reset during watchdog failed", exc_info=True)
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
                    self._instrument(pc, track)
                    asyncio.ensure_future(self._receive_video(track))
                elif track.kind == "audio" and self._audio_callback:
                    asyncio.ensure_future(self._receive_audio(track))

            @pc.on("connectionstatechange")
            async def on_state():
                state = pc.connectionState
                _LOGGER.debug("WebRTC state: %s", state)
                if pc is not self._pc:
                    return  # a replaced session closing — expected, not a failure
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
            _grow_rcvbuf(pc)

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

            asyncio.ensure_future(self._keepalive_loop(pc))

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

    def _instrument(self, pc: RTCPeerConnection, track) -> None:
        """Count packets/NACKs/PLIs and install the fast-skip jitter buffer."""
        rx = next((r for r in pc.getReceivers() if r.track is track), None)
        if rx is None:
            return
        st = self._vstats
        handle, nack, pli = rx._handle_rtp_packet, rx._send_rtcp_nack, rx._send_rtcp_pli

        async def _rtp(packet, arrival_time_ms):
            st.on_packet(packet.sequence_number)
            return await handle(packet, arrival_time_ms)

        async def _nack(media_ssrc, lost):
            st.nacks += 1
            return await nack(media_ssrc, lost)

        async def _pli(media_ssrc):
            st.plis += 1
            return await pli(media_ssrc)

        rx._handle_rtp_packet, rx._send_rtcp_nack, rx._send_rtcp_pli = _rtp, _nack, _pli
        rx._RTCRtpReceiver__jitter_buffer = _FastSkipJitterBuffer(st)

    async def _receive_video(self, track) -> None:
        _LOGGER.info("H264 video stream started")
        while True:
            try:
                frame = await track.recv()
                self._last_frame = time.monotonic()
                self._stalls = 0
                t = time.monotonic()
                self._frame_callback(frame)
                self._vstats.on_frame(bool(getattr(frame, "key_frame", False)), time.monotonic() - t)
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

    async def _keepalive_loop(self, pc: RTCPeerConnection) -> None:
        # Bound to ONE session: exits as soon as that pc is replaced. (It used to
        # check self._pc, so every past session's loop kept running — thousands
        # of keepAlive publishes every 5 s after a day of reconnects.)
        while pc is self._pc and pc.connectionState not in ("closed", "failed"):
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