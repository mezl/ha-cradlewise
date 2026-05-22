"""Cradlewise binary sensors."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
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
        OnlineSensor(coord),
        BabyPresent(coord),
        BabyNeedsAttention(coord),
        BabyNeedsHelp(coord),
        CribSoothing(coord),
        Bouncing(coord),
        MusicPlaying(coord),
        NightLight(coord),
        LoudSoundDetected(coord),
        InSleepSchedule(coord),
        InSoothingWindow(coord),
        RockingNotEffective(coord),
    ])


class _Base(BinarySensorEntity):
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


class OnlineSensor(_Base):
    _attr_name = "Online"
    _attr_unique_id = f"{CRADLE_ID}_online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return self._coord.available


class BabyPresent(_Base):
    _attr_name = "Baby Present"
    _attr_unique_id = f"{CRADLE_ID}_baby_present"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:baby"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("babyPresent"))


class BabyNeedsAttention(_Base):
    _attr_name = "Baby Needs Attention"
    _attr_unique_id = f"{CRADLE_ID}_baby_needs_attention"
    _attr_icon = "mdi:alert"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("attentionRequired"))


class BabyNeedsHelp(_Base):
    _attr_name = "Baby Needs Help"
    _attr_unique_id = f"{CRADLE_ID}_baby_needs_help"
    _attr_icon = "mdi:help-circle"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("babyNeedsHelp"))


class CribSoothing(_Base):
    _attr_name = "Crib Soothing"
    _attr_unique_id = f"{CRADLE_ID}_crib_helping"
    _attr_icon = "mdi:baby-carriage"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("isCribHelping"))


class Bouncing(_Base):
    _attr_name = "Bouncing"
    _attr_unique_id = f"{CRADLE_ID}_bouncing"
    _attr_icon = "mdi:baby-carriage"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.get_actuator().get("on"))


class MusicPlaying(_Base):
    _attr_name = "Music Playing"
    _attr_unique_id = f"{CRADLE_ID}_music_playing"
    _attr_icon = "mdi:music"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.get_sound().get("play"))


class NightLight(_Base):
    _attr_name = "Night Light"
    _attr_unique_id = f"{CRADLE_ID}_light_on"
    _attr_icon = "mdi:led-on"

    @property
    def is_on(self) -> bool:
        return int(self._coord.get_light().get("indicatorBrightness", 0)) > 0


class LoudSoundDetected(_Base):
    _attr_name = "Loud Sound Detected"
    _attr_unique_id = f"{CRADLE_ID}_loud_sound_detected"
    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_icon = "mdi:volume-high"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("loudSoundDetected"))


class InSleepSchedule(_Base):
    _attr_name = "In Sleep Schedule"
    _attr_unique_id = f"{CRADLE_ID}_inside_sleep_schedule"
    _attr_icon = "mdi:calendar-clock"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("insideSleepSchedule"))


class InSoothingWindow(_Base):
    _attr_name = "In Soothing Window"
    _attr_unique_id = f"{CRADLE_ID}_inside_soothing_window"
    _attr_icon = "mdi:clock-check"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("insideSoothingWindow"))


class RockingNotEffective(_Base):
    _attr_name = "Rocking Not Effective"
    _attr_unique_id = f"{CRADLE_ID}_rocking_not_effective"
    _attr_icon = "mdi:alert-circle"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("rockingNotEffective"))
