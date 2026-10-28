"""Cradlewise camera entity — live H264 via WebRTC-over-local-MQTT, with audio streaming."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import queue
import struct
import threading
from threading import Lock
from typing import Callable

from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import yamnet
from .const import DOMAIN, CRADLE_ID
from .cry_dsp import CryDetector
from .webrtc import CradlewiseVideoReceiver

_LOGGER = logging.getLogger(__name__)

_DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, CRADLE_ID)},
    manufacturer="Cradlewise",
    model="Smart Crib",
    name="Cradlewise avo",
)

# ── WAV header ─────────────────────────────────────────────────────────────────


def _make_wav_header(sample_rate: int, channels: int, bits: int = 16) -> bytes:
    """WAV header for streaming: data size = 0xFFFFFFFF (infinite stream)."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 0xFFFFFFFF,
        b"WAVE",
        b"fmt ", 16,
        1,            # PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data", 0xFFFFFFFF,
    )


# ── Platform setup ─────────────────────────────────────────────────────────────


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    cam = CradlewiseCameraEntity(hass)
    # Store reference so binary_sensor and services can reach it without coupling.
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["camera"] = cam
    async_add_entities([cam])
    # Register MJPEG and WAV endpoints.
    for view in (CribMjpegView(cam), CribAudioView(cam)):
        try:
            hass.http.register_view(view)
        except Exception:
            pass  # Already registered on reload
    _LOGGER.debug("Registered /api/cradlewise_avo/mjpeg and /api/cradlewise_avo/audio")


# ── HTTP views ─────────────────────────────────────────────────────────────────


class CribMjpegView(HomeAssistantView):
    """Unauthenticated MJPEG stream for Frigate NVR ingest."""

    url = "/api/cradlewise_avo/mjpeg"
    name = "api:cradlewise_avo:mjpeg"
    requires_auth = False  # Frigate container needs access without token negotiation

    def __init__(self, cam: "CradlewiseCameraEntity") -> None:
        self._cam = cam

    async def get(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=crib",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        _LOGGER.debug("MJPEG client connected: %s", request.remote)
        last: bytes | None = None
        try:
            while True:
                with self._cam._lock:
                    jpeg = self._cam._jpeg
                if jpeg is not None and jpeg is not last:
                    last = jpeg
                    await response.write(
                        b"--crib\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n" + jpeg + b"\r\n"
                    )
                await asyncio.sleep(0.05)  # poll at up to 20 fps
        except Exception:
            pass
        return response


class CribAudioView(HomeAssistantView):
    """WAV audio stream from the crib microphone."""

    url = "/api/cradlewise_avo/audio"
    name = "api:cradlewise_avo:audio"
    requires_auth = False

    def __init__(self, cam: "CradlewiseCameraEntity") -> None:
        self._cam = cam

    async def get(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "audio/wav",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        sr = self._cam._audio_sample_rate
        ch = self._cam._audio_channels
        await response.write(_make_wav_header(sr, ch))

        q: asyncio.Queue = asyncio.Queue(maxsize=300)
        self._cam._audio_subs.append(q)
        _LOGGER.debug("Audio stream client connected from %s (sr=%d ch=%d)", request.remote, sr, ch)

        silence_chunk = bytes(sr // 10 * ch * 2)  # 100ms silence
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(q.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    chunk = silence_chunk  # keep-alive padding
                await response.write(chunk)
        except Exception:
            pass
        finally:
            try:
                self._cam._audio_subs.remove(q)
            except ValueError:
                pass
            _LOGGER.debug("Audio stream client disconnected")

        return response


# ── Camera entity ──────────────────────────────────────────────────────────────


class CradlewiseCameraEntity(Camera):
    _attr_name = "Crib Camera"
    _attr_unique_id = f"{CRADLE_ID}_camera"
    _attr_device_info = _DEVICE_INFO
    _attr_is_streaming = True
    # HA's "live" view (and camera_proxy_stream) re-sends async_camera_image()
    # every frame_interval; the default 0.5 s capped the crib view at 2 fps even
    # though WebRTC delivers ~10 fps. Unchanged frames are not re-sent.
    _attr_frame_interval = 0.1
    _attr_icon = "mdi:cctv"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.hass = hass
        self._lock = Lock()
        self._jpeg: bytes | None = None
        self._receiver: CradlewiseVideoReceiver | None = None
        # Decoded frames → JPEG on a worker thread (off the event loop).
        self._frame_slot = None
        self._frame_evt = threading.Event()
        self._frame_stop = threading.Event()
        self._frame_thread: threading.Thread | None = None

        # Audio streaming
        self._audio_sample_rate = 48000
        self._audio_channels = 2
        self._audio_subs: list[asyncio.Queue] = []

        # Cry detection state (DSP — hand-tuned spectral gates)
        self._cry_detector = CryDetector()
        self._cry_listeners: list[Callable[[bool], None]] = []

        # Cry detection state (YAMNet — learned classifier, runs alongside the DSP one).
        # The model is loaded lazily off the event loop; inference runs on a worker
        # thread so it never blocks the audio callback. Absent runtime/model → disabled.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._yamnet: yamnet.CryClassifier | None = None
        self._yamnet_q: "queue.Queue" = queue.Queue(maxsize=8)
        self._yamnet_thread: threading.Thread | None = None
        self._yamnet_stop = threading.Event()
        self._yamnet_is_crying = False
        self._yamnet_prob = 0.0
        self._yamnet_listeners: list[Callable[[bool], None]] = []
        self._yamnet_prob_listeners: list[Callable[[float], None]] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._frame_stop.clear()
        self._frame_thread = threading.Thread(
            target=self._frame_worker, name="cradlewise-jpeg", daemon=True
        )
        self._frame_thread.start()
        self._receiver = CradlewiseVideoReceiver(self._on_frame, self._on_audio_frame)
        self._receiver.start(loop)
        # Load YAMNet off the event loop (model init is blocking); start its worker.
        self._yamnet = await self.hass.async_add_executor_job(
            yamnet.build_default, os.path.dirname(__file__)
        )
        if self._yamnet is not None:
            self._yamnet_stop.clear()
            self._yamnet_thread = threading.Thread(
                target=self._yamnet_worker, name="cradlewise-yamnet", daemon=True
            )
            self._yamnet_thread.start()
            _LOGGER.info("YAMNet cry classifier enabled")
        else:
            _LOGGER.info(
                "YAMNet cry classifier disabled (runtime or model files missing); "
                "DSP detector unaffected"
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._receiver:
            self._receiver.stop()
        self._frame_stop.set()
        self._frame_evt.set()
        self._yamnet_stop.set()
        try:
            self._yamnet_q.put_nowait(None)  # wake the worker so it can exit
        except queue.Full:
            pass

    # ── Cry detection listener registration ───────────────────────────────────

    def add_cry_listener(self, cb: Callable[[bool], None]) -> Callable:
        """Register a callback for DSP cry state changes. Returns an unsubscribe fn."""
        self._cry_listeners.append(cb)
        def _remove() -> None:
            try:
                self._cry_listeners.remove(cb)
            except ValueError:
                pass
        return _remove

    def add_yamnet_listener(self, cb: Callable[[bool], None]) -> Callable:
        """Register a callback for YAMNet cry state changes. Returns an unsubscribe fn."""
        self._yamnet_listeners.append(cb)
        def _remove() -> None:
            try:
                self._yamnet_listeners.remove(cb)
            except ValueError:
                pass
        return _remove

    def add_yamnet_prob_listener(self, cb: Callable[[float], None]) -> Callable:
        """Register a callback for YAMNet cry-probability updates. Returns an unsubscribe fn."""
        self._yamnet_prob_listeners.append(cb)
        def _remove() -> None:
            try:
                self._yamnet_prob_listeners.remove(cb)
            except ValueError:
                pass
        return _remove

    # ── Two-way audio ──────────────────────────────────────────────────────────

    def send_audio(self, pcm: bytes) -> None:
        """Send raw s16le stereo 48 kHz PCM to the crib speakers."""
        if self._receiver is not None:
            self._receiver.push_audio(pcm)

    # ── Frame callbacks ────────────────────────────────────────────────────────

    def _on_frame(self, frame) -> None:
        """Hand the decoded frame to the JPEG worker; never block the HA loop.

        Latest frame wins: if the worker is still busy, the older frame is dropped."""
        self._frame_slot = frame
        self._frame_evt.set()

    def _frame_worker(self) -> None:
        from PIL import ImageDraw, ImageFont
        import time as _t
        try:
            font = ImageFont.load_default(size=22)
        except TypeError:
            font = ImageFont.load_default()
        count = 0
        while not self._frame_stop.is_set():
            if not self._frame_evt.wait(1.0):
                continue
            self._frame_evt.clear()
            frame, self._frame_slot = self._frame_slot, None
            if frame is None:
                continue
            try:
                img = frame.to_image()
                # Burned-in "streaming vs stalled" indicator: incrementing counter + clock.
                # If the number/time is frozen on the crib view, the stream has stalled.
                count += 1
                label = "CRIB #%d  %s" % (count, _t.strftime("%H:%M:%S"))
                draw = ImageDraw.Draw(img)
                for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                    draw.text((8 + dx, 8 + dy), label, fill=(0, 0, 0), font=font)
                draw.text((8, 8), label, fill=(0, 255, 0), font=font)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=70)
                with self._lock:
                    self._jpeg = buf.getvalue()
            except Exception as e:
                _LOGGER.debug("Frame convert error: %s", e)

    def _on_audio_frame(self, frame) -> None:
        """Convert av.AudioFrame → s16le PCM, feed cry detector, broadcast to WAV clients."""
        try:
            import numpy as np

            self._audio_sample_rate = frame.sample_rate
            self._audio_channels = len(frame.layout.channels)

            raw = frame.to_ndarray()
            fmt = frame.format.name

            _FLOAT_FMTS = ("fltp", "flt", "f32p", "f32", "dblp", "dbl", "f64p", "f64")
            if fmt in _FLOAT_FMTS:
                # Float planar/packed: shape (channels, samples) or (1, channels*samples)
                f32 = raw.astype(np.float32)
                # Mono float for cry detection
                if f32.ndim == 2 and f32.shape[0] > 1:
                    mono = np.clip(f32.mean(axis=0), -1.0, 1.0)
                else:
                    mono = np.clip(f32.flatten(), -1.0, 1.0)
                # s16le for streaming
                s16 = np.clip(raw, -1.0, 1.0)
                s16 = (s16 * 32767).astype(np.int16)
            else:
                # Integer formats (s16, s16p, s32, …)
                i16 = raw.astype(np.int16)
                if i16.ndim == 2 and i16.shape[0] > 1:
                    # Planar: shape (channels, samples) — average across channels
                    mono = i16.mean(axis=0).astype(np.float32) / 32768.0
                else:
                    # Packed interleaved stereo: shape (1, channels*samples)
                    # Deinterleave L/R then average to produce true mono
                    flat = i16.flatten()
                    n_ch = self._audio_channels if self._audio_channels > 1 else 1
                    if n_ch > 1:
                        mono = flat.reshape(-1, n_ch).mean(axis=1).astype(np.float32) / 32768.0
                    else:
                        mono = flat.astype(np.float32) / 32768.0
                s16 = i16

            # Interleave planar channels → packed for the WAV stream
            if s16.ndim == 2:
                s16 = s16.T.flatten()
            pcm = s16.tobytes()

            # Cry detection (always runs, regardless of WAV subscribers)
            self._update_cry_detection(mono, frame.sample_rate)

            # Hand the same mono audio to the YAMNet worker (non-blocking; drop if busy).
            if self._yamnet is not None:
                try:
                    self._yamnet_q.put_nowait((mono.copy(), frame.sample_rate))
                except queue.Full:
                    pass  # worker still busy on the previous chunk; skip this frame

            # WAV stream clients
            for q in list(self._audio_subs):
                try:
                    q.put_nowait(pcm)
                except asyncio.QueueFull:
                    pass  # slow client, drop frame

        except Exception as e:
            _LOGGER.debug("Audio frame error: %s", e)

    # ── Cry detection ──────────────────────────────────────────────────────────

    def _update_cry_detection(self, mono: "np.ndarray", sample_rate: int) -> None:
        """Feed audio to the DSP detector; broadcast on debounced state change."""
        changed = self._cry_detector.feed(mono, sample_rate)
        if changed is None:
            return
        _LOGGER.debug("Baby crying state → %s", changed)
        # Notify listeners (already on the event loop via aiortc's async context)
        for cb in list(self._cry_listeners):
            cb(changed)
        # Also fire HA bus event so any other consumers can listen
        self.hass.bus.async_fire("cradlewise_baby_crying", {"is_crying": changed})

    # ── YAMNet cry detection (worker thread; keeps ML off the audio callback) ────

    def _yamnet_worker(self) -> None:
        """Pull mono chunks, run YAMNet inference, post results back to the loop."""
        while not self._yamnet_stop.is_set():
            try:
                item = self._yamnet_q.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            mono, sr = item
            try:
                changed, prob = self._yamnet.feed(mono, sr)
            except Exception as e:  # never let inference kill the worker
                _LOGGER.debug("YAMNet inference error: %s", e)
                continue
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._on_yamnet_result, changed, prob)

    def _on_yamnet_result(self, changed: bool | None, prob: float) -> None:
        """Runs on the event loop: update probability + debounced state, notify listeners."""
        self._yamnet_prob = prob
        for cb in list(self._yamnet_prob_listeners):
            cb(prob)
        if changed is None:
            return
        self._yamnet_is_crying = changed
        _LOGGER.debug("Baby crying (YAMNet) state → %s (p=%.2f)", changed, prob)
        for cb in list(self._yamnet_listeners):
            cb(changed)
        self.hass.bus.async_fire("cradlewise_yamnet_crying", {"is_crying": changed})

    @property
    def is_crying(self) -> bool:
        return self._cry_detector.is_crying

    @property
    def yamnet_is_crying(self) -> bool:
        return self._yamnet_is_crying

    @property
    def yamnet_prob(self) -> float:
        return self._yamnet_prob

    @property
    def yamnet_enabled(self) -> bool:
        return self._yamnet is not None

    # ── HA Camera interface ────────────────────────────────────────────────────

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        with self._lock:
            return self._jpeg
