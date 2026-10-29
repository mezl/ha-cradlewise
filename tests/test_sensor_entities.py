"""Unit tests for Cradlewise sensor entity native_value mappings."""
import sys, types, pytest
from unittest.mock import MagicMock


def _stub_paho():
    if "paho.mqtt.client" not in sys.modules:
        paho = types.ModuleType("paho")
        paho_mqtt = types.ModuleType("paho.mqtt")
        paho_mqtt_client = types.ModuleType("paho.mqtt.client")
        paho_mqtt_client.Client = MagicMock
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = paho_mqtt
        sys.modules["paho.mqtt.client"] = paho_mqtt_client


def make_coord(state: dict):
    _stub_paho()
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from custom_components.cradlewise.coordinator import CradlewiseCoordinator
    c = CradlewiseCoordinator.__new__(CradlewiseCoordinator)
    c.state = state
    c.available = True
    return c


def make_entity(cls_name: str, state: dict):
    import importlib
    mod = importlib.import_module("custom_components.cradlewise.sensor")
    cls = getattr(mod, cls_name)
    coord = make_coord(state)
    ent = cls.__new__(cls)
    ent._coord = coord
    ent._unsub = None
    return ent


# ── Sleep state sensors ───────────────────────────────────────────────────────

class TestSleepState:
    def test_maps_to_string(self):
        ent = make_entity("SleepState", {"babySleepState": 4})
        assert ent.native_value == "sleep"

    def test_none_when_missing(self):
        ent = make_entity("SleepState", {})
        assert ent.native_value is None

    def test_away_maps(self):
        ent = make_entity("SleepState", {"babySleepState": 0})
        assert ent.native_value == "away"


class TestSleepPhase:
    def test_awake(self):
        ent = make_entity("SleepPhase", {"babySleepPhase": 1})
        assert ent.native_value == "awake"

    def test_sleep(self):
        ent = make_entity("SleepPhase", {"babySleepPhase": 4})
        assert ent.native_value == "sleep"

    def test_none_when_missing(self):
        ent = make_entity("SleepPhase", {})
        assert ent.native_value is None


# ── Actuator sensors ──────────────────────────────────────────────────────────

class TestBounceAmplitudeSensor:
    def test_reads_from_actuator(self):
        ent = make_entity("BounceAmplitudeSensor", {"actuator": {"bounceAlwaysOnIntensity": 3}})
        assert ent.native_value == 3

    def test_none_when_not_set(self):
        ent = make_entity("BounceAmplitudeSensor", {"actuator": {}})
        assert ent.native_value is None


# ── Sound sensors ─────────────────────────────────────────────────────────────

class TestMusicVolumeSensor:
    def test_reads_volume(self):
        ent = make_entity("MusicVolumeSensor", {"soundSynth": {"volume": 45}})
        assert ent.native_value == 45

    def test_none_when_not_set(self):
        ent = make_entity("MusicVolumeSensor", {})
        assert ent.native_value is None


class TestMusicMood:
    def test_reads_mood(self):
        ent = make_entity("MusicMood", {"soundSynth": {"mood": "whitenoise"}})
        assert ent.native_value == "whitenoise"


# ── Light sensor ──────────────────────────────────────────────────────────────

class TestLightIntensitySensor:
    def test_reads_brightness(self):
        ent = make_entity("LightIntensitySensor", {"light": {"indicatorBrightness": 80}})
        assert ent.native_value == 80


# ── Environment sensors ───────────────────────────────────────────────────────

class TestTemperature:
    def test_top_level_key(self):
        ent = make_entity("Temperature", {"temperature": 22.5})
        assert ent.native_value == pytest.approx(22.5)

    def test_nested_sensorData(self):
        ent = make_entity("Temperature", {"sensorData": {"temperature": 21.0}})
        assert ent.native_value == pytest.approx(21.0)

    def test_nested_ambientSensor(self):
        ent = make_entity("Temperature", {"ambientSensor": {"temperature": 23.1}})
        assert ent.native_value == pytest.approx(23.1)

    def test_none_when_missing(self):
        ent = make_entity("Temperature", {})
        assert ent.native_value is None

    def test_top_level_takes_precedence(self):
        ent = make_entity("Temperature", {"temperature": 25.0, "sensorData": {"temperature": 20.0}})
        assert ent.native_value == pytest.approx(25.0)


class TestHumidity:
    def test_top_level_key(self):
        ent = make_entity("Humidity", {"humidity": 58.0})
        assert ent.native_value == pytest.approx(58.0)

    def test_nested_sensorData(self):
        ent = make_entity("Humidity", {"sensorData": {"humidity": 62.0}})
        assert ent.native_value == pytest.approx(62.0)

    def test_none_when_missing(self):
        ent = make_entity("Humidity", {})
        assert ent.native_value is None


class TestNoiseLevel:
    def test_noiseLevel_key(self):
        ent = make_entity("NoiseLevel", {"noiseLevel": 45.0})
        assert ent.native_value == pytest.approx(45.0)

    def test_snake_case_fallback(self):
        ent = make_entity("NoiseLevel", {"noise_level": 38.0})
        assert ent.native_value == pytest.approx(38.0)

    def test_nested_sensorData(self):
        ent = make_entity("NoiseLevel", {"sensorData": {"noiseLevel": 50.0}})
        assert ent.native_value == pytest.approx(50.0)

    def test_none_when_missing(self):
        ent = make_entity("NoiseLevel", {})
        assert ent.native_value is None


class TestBatteryLife:
    def test_reads_from_deviceStatus(self):
        ent = make_entity("BatteryLife", {"deviceStatus": {"batteryLife": 87}})
        assert ent.native_value == 87

    def test_none_when_missing(self):
        ent = make_entity("BatteryLife", {})
        assert ent.native_value is None

    def test_none_when_deviceStatus_empty(self):
        ent = make_entity("BatteryLife", {"deviceStatus": {}})
        assert ent.native_value is None


# ── Analytics sensors ─────────────────────────────────────────────────────────

class TestTotalSleepToday:
    def test_totalSleepDuration(self):
        ent = make_entity("TotalSleepToday", {"dayStats": {"totalSleepDuration": 420}})
        assert ent.native_value == 420

    def test_totalSleep_fallback(self):
        ent = make_entity("TotalSleepToday", {"dayStats": {"totalSleep": 300}})
        assert ent.native_value == 300

    def test_none_when_no_dayStats(self):
        ent = make_entity("TotalSleepToday", {})
        assert ent.native_value is None


class TestNapCount:
    def test_reads_napCount(self):
        ent = make_entity("NapCount", {"dayStats": {"napCount": 3}})
        assert ent.native_value == 3

    def test_none_when_missing(self):
        ent = make_entity("NapCount", {})
        assert ent.native_value is None


class TestLongestNap:
    def test_longestNapDuration(self):
        ent = make_entity("LongestNap", {"dayStats": {"longestNapDuration": 90}})
        assert ent.native_value == 90

    def test_longestNap_fallback(self):
        ent = make_entity("LongestNap", {"dayStats": {"longestNap": 60}})
        assert ent.native_value == 60


class TestSootheCount:
    def test_reads_sootheCount(self):
        ent = make_entity("SootheCount", {"dayStats": {"sootheCount": 2}})
        assert ent.native_value == 2


class TestSleepTime:
    def test_reads_sleepTime(self):
        ent = make_entity("SleepTime", {"sleepTime": "2026-05-22T01:30:00Z"})
        assert ent.native_value == "2026-05-22T01:30:00Z"

    def test_lastSleepTime_fallback(self):
        ent = make_entity("SleepTime", {"lastSleepTime": "2026-05-22T02:00:00Z"})
        assert ent.native_value == "2026-05-22T02:00:00Z"

    def test_none_when_missing(self):
        ent = make_entity("SleepTime", {})
        assert ent.native_value is None


class TestWakeUpTime:
    def test_reads_wakeUpTime(self):
        ent = make_entity("WakeUpTime", {"wakeUpTime": "2026-05-22T07:00:00Z"})
        assert ent.native_value == "2026-05-22T07:00:00Z"


class TestLastNapEnded:
    def test_dayStats_lastNapEnd(self):
        ent = make_entity("LastNapEnded", {"dayStats": {"lastNapEnd": "2026-05-22T15:00:00Z"}})
        assert ent.native_value == "2026-05-22T15:00:00Z"

    def test_top_level_fallback(self):
        ent = make_entity("LastNapEnded", {"lastNapEnd": "2026-05-22T14:00:00Z"})
        assert ent.native_value == "2026-05-22T14:00:00Z"

    def test_none_when_missing(self):
        ent = make_entity("LastNapEnded", {})
        assert ent.native_value is None
