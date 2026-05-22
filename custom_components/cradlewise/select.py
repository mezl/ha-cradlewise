"""Cradlewise select entities."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CRADLE_ID
from .coordinator import CradlewiseCoordinator

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
    coord: CradlewiseCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SoundProfileSelect(coord)])


class _Base(SelectEntity):
    _attr_should_poll = False

    def __init__(self, coord: CradlewiseCoordinator) -> None:
        self._coord = coord
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        self._unsub = self._coord.async_add_listener(self._on_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()

    @callback
    def _on_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._coord.available

    @property
    def device_info(self) -> DeviceInfo:
        return _DEVICE_INFO


class SoundProfileSelect(_Base):
    """Select the white-noise sound profile sent to the crib."""

    _attr_name = "Sound Profile"
    _attr_unique_id = f"{CRADLE_ID}_sound_profile"
    _attr_icon = "mdi:sine-wave"
    _attr_options = ["White Noise", "Deep Sleeper", "Noisy Room", "Sensitive Sleeper"]

    # Display name → MQTT value sent in soundSynth.mood
    _TO_MQTT: dict[str, str] = {
        "White Noise": "whiteNoise",
        "Deep Sleeper": "deepSleeper",
        "Noisy Room": "noisyRoom",
        "Sensitive Sleeper": "sensitiveSleeper",
    }
    _FROM_MQTT: dict[str, str] = {v: k for k, v in _TO_MQTT.items()}

    @property
    def current_option(self) -> str | None:
        raw = self._coord.get_sound().get("mood")
        if raw is None:
            return None
        return self._FROM_MQTT.get(str(raw), str(raw))

    def select_option(self, option: str) -> None:
        self._coord.send_desired({"soundSynth": {"mood": self._TO_MQTT.get(option, option)}})
