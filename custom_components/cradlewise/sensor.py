"""Cradlewise sensor entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CRADLE_ID, SLEEP_STATE_MAP
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
        NapCountToday(coord),
        TotalSleepToday(coord),
        LongestNapToday(coord),
        LastNapStart(coord),
        LastNapEnd(coord),
        SoothingCountToday(coord),
        TotalAwakeToday(coord),
        SleepState(coord),
        SleepPhase(coord),
        CradleMode(coord),
        BounceMode(coord),
        BounceSetting(coord),
        BounceAmplitudeSensor(coord),
        ResponsivitySetting(coord),
        MusicMood(coord),
        MusicVolumeSensor(coord),
        MusicMode(coord),
        LightIntensitySensor(coord),
        MusicTrack(coord),
        RoomTemperature(coord),
        FirmwareVersion(coord),
        FirmwareUpdateVersion(coord),
    ])


class _Base(SensorEntity):
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


class SleepState(_Base):
    _attr_name = "Sleep State"
    _attr_unique_id = f"{CRADLE_ID}_sleep_state"
    _attr_icon = "mdi:sleep"

    @property
    def native_value(self) -> str | None:
        raw = self._coord.state.get("babySleepState")
        if raw is None:
            return None
        return SLEEP_STATE_MAP.get(int(raw), str(raw))


class SleepPhase(_Base):
    _attr_name = "Sleep Phase"
    _attr_unique_id = f"{CRADLE_ID}_sleep_phase"
    _attr_icon = "mdi:sleep"

    @property
    def native_value(self) -> str | None:
        raw = self._coord.state.get("babySleepPhase")
        if raw is None:
            return None
        return SLEEP_STATE_MAP.get(int(raw), str(raw))


class CradleMode(_Base):
    _attr_name = "Cradle Mode"
    _attr_unique_id = f"{CRADLE_ID}_cradle_mode"
    _attr_icon = "mdi:crib"

    @property
    def native_value(self) -> str | None:
        return self._coord.state.get("mode")


class BounceMode(_Base):
    _attr_name = "Bounce Mode"
    _attr_unique_id = f"{CRADLE_ID}_bounce_mode"
    _attr_icon = "mdi:waves"

    @property
    def native_value(self):
        return self._coord.state.get("bounceMode")


class BounceSetting(_Base):
    _attr_name = "Bounce Setting"
    _attr_unique_id = f"{CRADLE_ID}_bounce_setting"
    _attr_icon = "mdi:tune"

    @property
    def native_value(self):
        return self._coord.state.get("bounceSetting")


class BounceAmplitudeSensor(_Base):
    _attr_name = "Bounce Amplitude"
    _attr_unique_id = f"{CRADLE_ID}_bounce_amplitude"
    _attr_icon = "mdi:waves"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._coord.get_actuator().get("bounceAlwaysOnIntensity")


class ResponsivitySetting(_Base):
    _attr_name = "Responsivity"
    _attr_unique_id = f"{CRADLE_ID}_responsivity_setting"
    _attr_icon = "mdi:tune"

    @property
    def native_value(self):
        return self._coord.state.get("responsivitySetting")


class MusicMood(_Base):
    _attr_name = "Music Mood"
    _attr_unique_id = f"{CRADLE_ID}_music_mood"
    _attr_icon = "mdi:music-note"

    @property
    def native_value(self) -> str | None:
        return self._coord.get_sound().get("mood")


class MusicVolumeSensor(_Base):
    _attr_name = "Music Volume"
    _attr_unique_id = f"{CRADLE_ID}_music_volume"
    _attr_icon = "mdi:volume-high"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._coord.get_sound().get("volume")


class MusicMode(_Base):
    _attr_name = "Music Mode"
    _attr_unique_id = f"{CRADLE_ID}_music_mode"
    _attr_icon = "mdi:music"

    @property
    def native_value(self):
        return self._coord.state.get("musicMode")


class LightIntensitySensor(_Base):
    _attr_name = "Light Intensity"
    _attr_unique_id = f"{CRADLE_ID}_light_intensity"
    _attr_icon = "mdi:brightness-6"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._coord.get_light().get("indicatorBrightness")


class MusicTrack(_Base):
    _attr_name = "Music Track"
    _attr_unique_id = f"{CRADLE_ID}_music_track"
    _attr_icon = "mdi:music-note"

    @property
    def native_value(self) -> str | None:
        return self._coord.get_sound().get("trackName")


class RoomTemperature(_Base):
    _attr_name = "Room Temperature"
    _attr_unique_id = f"{CRADLE_ID}_room_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float | None:
        raw = self._coord.get_device_status().get("ambientTemp")
        if raw is None:
            return None
        return round(raw / 1000, 1)


class FirmwareVersion(_Base):
    _attr_name = "Firmware Version"
    _attr_unique_id = f"{CRADLE_ID}_firmware_version"
    _attr_icon = "mdi:chip"

    @property
    def native_value(self) -> str | None:
        return self._coord.get_meta().get("software_version")


class FirmwareUpdateVersion(_Base):
    _attr_name = "Firmware Update Version"
    _attr_unique_id = f"{CRADLE_ID}_firmware_update_version"
    _attr_icon = "mdi:update"

    @property
    def native_value(self) -> str | None:
        upd = self._coord.get_update()
        if not upd.get("available"):
            return None
        return upd.get("version")


# ── Local sleep analytics (derived from MQTT state transitions) ───────────────

class _SleepAnalyticsBase(_Base):
    """Base for analytics sensors — reads from the coordinator's SleepTracker."""


class NapCountToday(_SleepAnalyticsBase):
    _attr_name = "Nap Count Today"
    _attr_unique_id = f"{CRADLE_ID}_nap_count_today"
    _attr_icon = "mdi:sleep"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int:
        return self._coord.sleep_tracker.nap_count

    @property
    def extra_state_attributes(self) -> dict:
        """Today's raw sleep/soothe timeline.

        The Sleep Insights card draws its day chart from these instead of
        recorder history, so the chart still works when recorder is down.
        """
        tracker = self._coord.sleep_tracker
        return {
            "naps": tracker.sessions,
            "awake_periods": tracker.awake_sessions,
            "soothing_events": tracker.soothing_events,
        }


class TotalSleepToday(_SleepAnalyticsBase):
    _attr_name = "Total Sleep Today"
    _attr_unique_id = f"{CRADLE_ID}_total_sleep_today"
    _attr_icon = "mdi:clock-time-eight"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float:
        return self._coord.sleep_tracker.total_sleep_minutes


class LongestNapToday(_SleepAnalyticsBase):
    _attr_name = "Longest Nap Today"
    _attr_unique_id = f"{CRADLE_ID}_longest_nap_today"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float | None:
        return self._coord.sleep_tracker.longest_nap_minutes


class LastNapStart(_SleepAnalyticsBase):
    _attr_name = "Last Nap Start"
    _attr_unique_id = f"{CRADLE_ID}_last_nap_start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:sleep"

    @property
    def native_value(self):
        return self._coord.sleep_tracker.last_nap_start


class LastNapEnd(_SleepAnalyticsBase):
    _attr_name = "Last Nap End"
    _attr_unique_id = f"{CRADLE_ID}_last_nap_end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:sleep-off"

    @property
    def native_value(self):
        return self._coord.sleep_tracker.last_nap_end


class SoothingCountToday(_SleepAnalyticsBase):
    _attr_name = "Soothing Count Today"
    _attr_unique_id = f"{CRADLE_ID}_soothing_count_today"
    _attr_icon = "mdi:baby-carriage"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int:
        return self._coord.sleep_tracker.soothing_count


class TotalAwakeToday(_SleepAnalyticsBase):
    _attr_name = "Total Awake Today"
    _attr_unique_id = f"{CRADLE_ID}_total_awake_today"
    _attr_icon = "mdi:weather-sunny"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    @property
    def native_value(self) -> float:
        return self._coord.sleep_tracker.total_awake_minutes
