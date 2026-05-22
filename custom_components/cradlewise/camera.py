"""Cradlewise camera entity — live H264 via WebRTC-over-local-MQTT."""
from __future__ import annotations

import asyncio
import io
import logging
from threading import Lock

from aiohttp import web

from homeassistant.components.camera import Camera
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CRADLE_ID
from .webrtc import CradlewiseVideoReceiver

_LOGGER = logging.getLogger(__name__)

_DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, CRADLE_ID)},
    manufacturer="Cradlewise",
    model="Smart Crib",
    name="Cradlewise avo",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    cam = CradlewiseCameraEntity()
    async_add_entities([cam])
    # Register the fast MJPEG endpoint for Frigate.
    # HA's built-in camera proxy is rate-limited; this view delivers frames at
    # full WebRTC rate so Frigate gets smooth video without a separate bridge.
    try:
        hass.http.register_view(CribMjpegView(cam))
        _LOGGER.debug("Registered /api/cradlewise_avo/mjpeg for Frigate")
    except Exception:
        # Already registered on a previous config-entry init (reload without restart).
        pass


class CribMjpegView(HomeAssistantView):
    """Unauthenticated MJPEG stream for Frigate NVR ingest."""

    url = "/api/cradlewise_avo/mjpeg"
    name = "api:cradlewise_avo:mjpeg"
    requires_auth = False  # Frigate container needs access without token negotiation

    def __init__(self, cam: CradlewiseCameraEntity) -> None:
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
                        b"--crib\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                    )
                await asyncio.sleep(0.05)  # poll at up to 20 fps
        except Exception:
            pass
        return response


class CradlewiseCameraEntity(Camera):
    _attr_name = "Crib Camera"
    _attr_unique_id = f"{CRADLE_ID}_camera"
    _attr_device_info = _DEVICE_INFO
    _attr_is_streaming = True
    _attr_icon = "mdi:cctv"

    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._jpeg: bytes | None = None
        self._receiver: CradlewiseVideoReceiver | None = None

    async def async_added_to_hass(self) -> None:
        loop = asyncio.get_running_loop()
        self._receiver = CradlewiseVideoReceiver(self._on_frame)
        self._receiver.start(loop)

    async def async_will_remove_from_hass(self) -> None:
        if self._receiver:
            self._receiver.stop()

    def _on_frame(self, frame) -> None:
        try:
            img = frame.to_image()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            with self._lock:
                self._jpeg = buf.getvalue()
        except Exception as e:
            _LOGGER.debug("Frame convert error: %s", e)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        with self._lock:
            return self._jpeg
