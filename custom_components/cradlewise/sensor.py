"""Cradlewise sensor entities."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
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
        # Environment sensors
        Temperature(coord),
        Humidity(coord),
        NoiseLevel(coord),
        BatteryLife(coord),
        # Analytics sensors (populated when crib reports dayStats via MQTT)
        TotalSleepToday(coord),
        TotalAwakeToday(coord),
        NapCount(coord),
        LongestNap(coord),
        SootheCount(coord),
        SleepTime(coord),
        WakeUpTime(coord),
        LastNapStarted(coord),
        LastNapEnded(coord),
        LastSleepEvent(coord),
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


# ── Environment sensors ──────────────────────────────────────────────────

class Temperature(_Base):
    _attr_name = "Temperature"
    _attr_unique_id = f"{CRADLE_ID}_temperature"
    _attr_icon = "mdi:thermometer"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        v = self._coord.state.get("temperature")
        if v is None:
            sensor_data = self._coord.state.get("sensorData") or self._coord.state.get("ambientSensor") or {}
            v = sensor_data.get("temperature")
        return float(v) if v is not None else None


class Humidity(_Base):
    _attr_name = "Humidity"
    _attr_unique_id = f"{CRADLE_ID}_humidity"
    _attr_icon = "mdi:water-percent"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        v = self._coord.state.get("humidity")
        if v is None:
            sensor_data = self._coord.state.get("sensorData") or self._coord.state.get("ambientSensor") or {}
            v = sensor_data.get("humidity")
        return float(v) if v is not None else None


class NoiseLevel(_Base):
    _attr_name = "Noise Level"
    _attr_unique_id = f"{CRADLE_ID}_noise_level"
    _attr_icon = "mdi:volume-medium"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        v = self._coord.state.get("noiseLevel") or self._coord.state.get("noise_level")
        if v is None:
            sensor_data = self._coord.state.get("sensorData") or {}
            v = sensor_data.get("noiseLevel")
        return float(v) if v is not None else None


class BatteryLife(_Base):
    _attr_name = "Battery Life"
    _attr_unique_id = f"{CRADLE_ID}_battery_life"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        v = (self._coord.state.get("deviceStatus") or {}).get("batteryLife")
        return int(v) if v is not None else None


# ── Analytics sensors (populated via dayStats shadow key or direct shadow keys) ──

class TotalSleepToday(_Base):
    _attr_name = "Total Sleep Today"
    _attr_unique_id = f"{CRADLE_ID}_total_sleep_today"
    _attr_icon = "mdi:sleep"
    _attr_native_unit_of_measurement = "min"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "total_sleep_today"

    @property
    def native_value(self):
        day = self._coord.state.get("dayStats") or {}
        v = day.get("totalSleepDuration") or day.get("totalSleep")
        return int(v) if v is not None else None


class TotalAwakeToday(_Base):
    _attr_name = "Total Awake Today"
    _attr_unique_id = f"{CRADLE_ID}_total_awake_today"
    _attr_icon = "mdi:weather-sunny"
    _attr_native_unit_of_measurement = "min"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "total_awake_today"

    @property
    def native_value(self):
        day = self._coord.state.get("dayStats") or {}
        v = day.get("totalAwakeDuration") or day.get("totalAwake")
        return int(v) if v is not None else None


class NapCount(_Base):
    _attr_name = "Nap Count"
    _attr_unique_id = f"{CRADLE_ID}_nap_count"
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "nap_count"

    @property
    def native_value(self):
        day = self._coord.state.get("dayStats") or {}
        v = day.get("napCount")
        return int(v) if v is not None else None


class LongestNap(_Base):
    _attr_name = "Longest Nap"
    _attr_unique_id = f"{CRADLE_ID}_longest_nap"
    _attr_icon = "mdi:trophy"
    _attr_native_unit_of_measurement = "min"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "longest_nap"

    @property
    def native_value(self):
        day = self._coord.state.get("dayStats") or {}
        v = day.get("longestNapDuration") or day.get("longestNap")
        return int(v) if v is not None else None


class SootheCount(_Base):
    _attr_name = "Soothe Count"
    _attr_unique_id = f"{CRADLE_ID}_soothe_count"
    _attr_icon = "mdi:hand-heart"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "soothe_count"

    @property
    def native_value(self):
        day = self._coord.state.get("dayStats") or {}
        v = day.get("sootheCount")
        return int(v) if v is not None else None


class SleepTime(_Base):
    _attr_name = "Sleep Time"
    _attr_unique_id = f"{CRADLE_ID}_sleep_time"
    _attr_icon = "mdi:clock-outline"
    _attr_translation_key = "sleep_time"

    @property
    def native_value(self) -> str | None:
        return self._coord.state.get("sleepTime") or self._coord.state.get("lastSleepTime")


class WakeUpTime(_Base):
    _attr_name = "Wake-up Time"
    _attr_unique_id = f"{CRADLE_ID}_wake_up_time"
    _attr_icon = "mdi:alarm"
    _attr_translation_key = "wake_up_time"

    @property
    def native_value(self) -> str | None:
        return self._coord.state.get("wakeUpTime") or self._coord.state.get("lastWakeUpTime")


class LastNapStarted(_Base):
    _attr_name = "Last Nap Started"
    _attr_unique_id = f"{CRADLE_ID}_last_nap_start"
    _attr_icon = "mdi:clock-start"
    _attr_translation_key = "last_nap_start"

    @property
    def native_value(self) -> str | None:
        day = self._coord.state.get("dayStats") or {}
        return day.get("lastNapStart") or self._coord.state.get("lastNapStart")


class LastNapEnded(_Base):
    _attr_name = "Last Nap Ended"
    _attr_unique_id = f"{CRADLE_ID}_last_nap_end"
    _attr_icon = "mdi:clock-end"
    _attr_translation_key = "last_nap_end"

    @property
    def native_value(self) -> str | None:
        day = self._coord.state.get("dayStats") or {}
        return day.get("lastNapEnd") or self._coord.state.get("lastNapEnd")


class LastSleepEvent(_Base):
    _attr_name = "Last Sleep Event"
    _attr_unique_id = f"{CRADLE_ID}_last_event"
    _attr_icon = "mdi:history"
    _attr_translation_key = "last_event"

    @property
    def native_value(self) -> str | None:
        return self._coord.state.get("lastEvent") or self._coord.state.get("lastSleepEvent")
