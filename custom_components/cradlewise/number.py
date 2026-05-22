"""Cradlewise number entities."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    async_add_entities([
        BounceAmplitude(coord),
        MusicVolume(coord),
        LightIntensity(coord),
        ResponsivitySetting(coord),
    ])


class _Base(NumberEntity):
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER

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


class BounceAmplitude(_Base):
    _attr_name = "Rock Amplitude"
    _attr_unique_id = f"{CRADLE_ID}_bounce_amplitude"
    _attr_icon = "mdi:waves"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1

    @property
    def native_value(self) -> float | None:
        # In smart mode firmware controls amplitude; bounceAlwaysOnIntensity is the
        # user-settable level that takes effect when forced mode is enabled.
        v = self._coord.get_actuator().get("bounceAlwaysOnIntensity")
        return float(v) if v is not None else None

    def set_native_value(self, value: float) -> None:
        # Setting intensity switches to forced (always-on) mode at that level.
        self._coord.send_desired({
            "actuator": {"bounceAlwaysOn": True, "bounceAlwaysOnIntensity": int(value)}
        })


class MusicVolume(_Base):
    _attr_name = "Music Volume"
    _attr_unique_id = f"{CRADLE_ID}_music_volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 60
    _attr_native_step = 1

    @property
    def native_value(self) -> float | None:
        v = self._coord.get_sound().get("volume")
        return float(v) if v is not None else None

    def set_native_value(self, value: float) -> None:
        self._coord.send_desired({"soundSynth": {"volume": int(value)}})


class LightIntensity(_Base):
    _attr_name = "Light Intensity"
    _attr_unique_id = f"{CRADLE_ID}_light_intensity"
    _attr_icon = "mdi:brightness-6"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    @property
    def native_value(self) -> float | None:
        v = self._coord.get_light().get("indicatorBrightness")
        return float(v) if v is not None else None

    def set_native_value(self, value: float) -> None:
        self._coord.send_desired({"light": {"indicatorBrightness": int(value)}})


class ResponsivitySetting(_Base):
    _attr_name = "Responsivity"
    _attr_unique_id = f"{CRADLE_ID}_responsivity_number"
    _attr_icon = "mdi:tune"
    _attr_native_min_value = 1
    _attr_native_max_value = 10
    _attr_native_step = 1

    @property
    def native_value(self) -> float | None:
        v = self._coord.state.get("responsivitySetting")
        return float(v) if v is not None else None

    def set_native_value(self, value: float) -> None:
        self._coord.send_desired({"responsivitySetting": int(value)})
