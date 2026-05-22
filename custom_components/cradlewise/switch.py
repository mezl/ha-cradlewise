"""Cradlewise switch entities."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
        RockingSwitch(coord),
        MusicSwitch(coord),
        NightLightSwitch(coord),
        AutoAmplitudeSwitch(coord),
        AutoVolumeSwitch(coord),
        AutoMoodSwitch(coord),
        KeepMusicOnDuringSleepSwitch(coord),
    ])


class _Base(SwitchEntity):
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


class RockingSwitch(_Base):
    _attr_name = "Rocking"
    _attr_unique_id = f"{CRADLE_ID}_rocking"
    _attr_icon = "mdi:baby-carriage"

    @property
    def is_on(self) -> bool:
        a = self._coord.get_actuator()
        return bool(a.get("on")) or bool(a.get("bounceAlwaysOn"))

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"actuator": {"on": True, "bounceAlwaysOn": False}})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"actuator": {"on": False, "bounceAlwaysOn": False}})


class MusicSwitch(_Base):
    _attr_name = "White Noise"
    _attr_unique_id = f"{CRADLE_ID}_music"
    _attr_icon = "mdi:music"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.get_sound().get("play"))

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"soundSynth": {"play": True}})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"soundSynth": {"play": False}})


class NightLightSwitch(_Base):
    _attr_name = "Night Light"
    _attr_unique_id = f"{CRADLE_ID}_night_light"
    _attr_icon = "mdi:led-on"

    @property
    def is_on(self) -> bool:
        brightness = self._coord.get_light().get("indicatorBrightness", 0)
        return int(brightness) > 0

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"light": {"indicatorBrightness": 100}})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"light": {"indicatorBrightness": 0}})


class AutoAmplitudeSwitch(_Base):
    _attr_name = "Auto Amplitude"
    _attr_unique_id = f"{CRADLE_ID}_auto_amplitude"
    _attr_icon = "mdi:waves-arrow-up"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("autoAmplitude"))

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"autoAmplitude": True})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"autoAmplitude": False})


class AutoVolumeSwitch(_Base):
    _attr_name = "Auto Volume"
    _attr_unique_id = f"{CRADLE_ID}_auto_volume"
    _attr_icon = "mdi:volume-vibrate"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("autoVolume"))

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"autoVolume": True})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"autoVolume": False})


class AutoMoodSwitch(_Base):
    _attr_name = "Auto Sound Mood"
    _attr_unique_id = f"{CRADLE_ID}_auto_mood"
    _attr_icon = "mdi:music-note-plus"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("autoMood"))

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"autoMood": True})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"autoMood": False})


class KeepMusicOnDuringSleepSwitch(_Base):
    _attr_name = "Keep Music During Sleep"
    _attr_unique_id = f"{CRADLE_ID}_keep_music_during_sleep"
    _attr_icon = "mdi:music-note-eighth"

    @property
    def is_on(self) -> bool:
        return bool(self._coord.state.get("keepMusicOnDuringSleep"))

    def turn_on(self, **kwargs) -> None:
        self._coord.send_desired({"keepMusicOnDuringSleep": True})

    def turn_off(self, **kwargs) -> None:
        self._coord.send_desired({"keepMusicOnDuringSleep": False})
