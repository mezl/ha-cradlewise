"""Camera entity — live H.264 from the crib via local WebRTC-over-MQTT.

Requires:
  - CONF_LOCAL_HOST set in the config entry (the crib's LAN IP address).
  - mTLS certs placed in custom_components/cradlewise/certs/
      amazon_root_ca1.pem, client.pem, client.key
    See certs/README.md for instructions.
"""
from __future__ import annotations

import asyncio
import io
import logging
from threading import Lock

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_LOCAL_HOST, DOMAIN
from .coordinator import CradlewiseCoordinator
from .sensor import _device_info
from .webrtc import CradlewiseVideoReceiver

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Cradlewise camera entities."""
    local_host: str | None = entry.data.get(CONF_LOCAL_HOST)
    if not local_host:
        _LOGGER.info(
            "No local_host configured — skipping camera setup. "
            "Re-configure the integration and enter the crib's LAN IP to enable video."
        )
        return

    coordinator: CradlewiseCoordinator = entry.runtime_data
    entities = [
        CradlewiseCameraEntity(cradle_id, local_host, coordinator)
        for cradle_id in coordinator.cradles
    ]
    async_add_entities(entities)


class CradlewiseCameraEntity(Camera):
    """Streams live H.264 video from one Cradlewise crib."""

    _attr_has_entity_name = True
    _attr_translation_key = "camera"
    _attr_is_streaming = True
    _attr_icon = "mdi:cctv"

    def __init__(
        self,
        cradle_id: str,
        local_host: str,
        coordinator: CradlewiseCoordinator,
    ) -> None:
        super().__init__()
        self._cradle_id = cradle_id
        self._coordinator = coordinator
        self._lock = Lock()
        self._jpeg: bytes | None = None
        self._receiver: CradlewiseVideoReceiver | None = None

        cradle = coordinator.cradles.get(cradle_id)
        self._attr_unique_id = f"{cradle_id}_camera"
        self._attr_name = "Camera"
        self._attr_device_info = _device_info(cradle) if cradle else None

        self._local_host = local_host

    async def async_added_to_hass(self) -> None:
        loop = asyncio.get_running_loop()
        self._receiver = CradlewiseVideoReceiver(
            host=self._local_host,
            cradle_id=self._cradle_id,
            frame_callback=self._on_frame,
        )
        self._receiver.start(loop)

    async def async_will_remove_from_hass(self) -> None:
        if self._receiver:
            self._receiver.stop()
            self._receiver = None

    def _on_frame(self, frame) -> None:
        try:
            img = frame.to_image()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
            with self._lock:
                self._jpeg = buf.getvalue()
        except Exception as e:
            _LOGGER.debug("Frame conversion error: %s", e)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        with self._lock:
            return self._jpeg
