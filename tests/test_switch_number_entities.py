"""Unit tests for switch and number entity state + commands."""
import sys, types, json, pytest
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
    c._client = MagicMock()
    c._listeners = []

    def send_desired(payload):
        msg = json.dumps({"state": {"desired": payload}})
        from custom_components.cradlewise.const import SHADOW_UPDATE
        c._client.publish(SHADOW_UPDATE, msg, qos=1)

    c.send_desired = send_desired
    return c


def make_switch(cls_name: str, state: dict):
    import importlib
    mod = importlib.import_module("custom_components.cradlewise.switch")
    cls = getattr(mod, cls_name)
    coord = make_coord(state)
    ent = cls.__new__(cls)
    ent._coord = coord
    ent._unsub = None
    return ent


def make_number(cls_name: str, state: dict):
    import importlib
    mod = importlib.import_module("custom_components.cradlewise.number")
    cls = getattr(mod, cls_name)
    coord = make_coord(state)
    ent = cls.__new__(cls)
    ent._coord = coord
    ent._unsub = None
    return ent


def last_published_payload(coord):
    call_args = coord._client.publish.call_args
    return json.loads(call_args[0][1])["state"]["desired"]


# ── RockingSwitch ─────────────────────────────────────────────────────────────

class TestRockingSwitch:
    def test_is_on_when_actuator_on(self):
        ent = make_switch("RockingSwitch", {"actuator": {"on": True}})
        assert ent.is_on is True

    def test_is_on_when_bounceAlwaysOn(self):
        ent = make_switch("RockingSwitch", {"actuator": {"on": False, "bounceAlwaysOn": True}})
        assert ent.is_on is True

    def test_is_off(self):
        ent = make_switch("RockingSwitch", {"actuator": {"on": False, "bounceAlwaysOn": False}})
        assert ent.is_on is False

    def test_is_off_when_no_actuator(self):
        ent = make_switch("RockingSwitch", {})
        assert ent.is_on is False

    def test_turn_on_sends_correct_desired(self):
        ent = make_switch("RockingSwitch", {})
        ent.turn_on()
        payload = last_published_payload(ent._coord)
        assert payload["actuator"]["on"] is True

    def test_turn_off_sends_correct_desired(self):
        ent = make_switch("RockingSwitch", {"actuator": {"on": True}})
        ent.turn_off()
        payload = last_published_payload(ent._coord)
        assert payload["actuator"]["on"] is False


# ── MusicSwitch ───────────────────────────────────────────────────────────────

class TestMusicSwitch:
    def test_is_on(self):
        ent = make_switch("MusicSwitch", {"soundSynth": {"play": True}})
        assert ent.is_on is True

    def test_is_off(self):
        ent = make_switch("MusicSwitch", {"soundSynth": {"play": False}})
        assert ent.is_on is False

    def test_turn_on(self):
        ent = make_switch("MusicSwitch", {})
        ent.turn_on()
        assert last_published_payload(ent._coord)["soundSynth"]["play"] is True

    def test_turn_off(self):
        ent = make_switch("MusicSwitch", {})
        ent.turn_off()
        assert last_published_payload(ent._coord)["soundSynth"]["play"] is False


# ── NightLightSwitch ──────────────────────────────────────────────────────────

class TestNightLightSwitch:
    def test_is_on_when_brightness_positive(self):
        ent = make_switch("NightLightSwitch", {"light": {"indicatorBrightness": 50}})
        assert ent.is_on is True

    def test_is_off_when_brightness_zero(self):
        ent = make_switch("NightLightSwitch", {"light": {"indicatorBrightness": 0}})
        assert ent.is_on is False

    def test_is_off_when_no_light(self):
        ent = make_switch("NightLightSwitch", {})
        assert ent.is_on is False

    def test_turn_on_sets_brightness_100(self):
        ent = make_switch("NightLightSwitch", {})
        ent.turn_on()
        assert last_published_payload(ent._coord)["light"]["indicatorBrightness"] == 100

    def test_turn_off_sets_brightness_0(self):
        ent = make_switch("NightLightSwitch", {})
        ent.turn_off()
        assert last_published_payload(ent._coord)["light"]["indicatorBrightness"] == 0


# ── BounceAmplitude number ────────────────────────────────────────────────────

class TestBounceAmplitude:
    def test_native_value(self):
        ent = make_number("BounceAmplitude", {"actuator": {"bounceAlwaysOnIntensity": 3}})
        assert ent.native_value == 3.0

    def test_none_when_not_set(self):
        ent = make_number("BounceAmplitude", {"actuator": {}})
        assert ent.native_value is None

    def test_set_value_sends_desired(self):
        ent = make_number("BounceAmplitude", {})
        ent.set_native_value(4)
        payload = last_published_payload(ent._coord)
        assert payload["actuator"]["bounceAlwaysOnIntensity"] == 4
        assert payload["actuator"]["bounceAlwaysOn"] is True

    def test_range_1_to_5(self):
        ent = make_number("BounceAmplitude", {})
        assert ent._attr_native_min_value == 1
        assert ent._attr_native_max_value == 5


# ── MusicVolume number ────────────────────────────────────────────────────────

class TestMusicVolume:
    def test_native_value(self):
        ent = make_number("MusicVolume", {"soundSynth": {"volume": 45}})
        assert ent.native_value == 45.0

    def test_none_when_not_set(self):
        ent = make_number("MusicVolume", {})
        assert ent.native_value is None

    def test_set_value(self):
        ent = make_number("MusicVolume", {})
        ent.set_native_value(30)
        payload = last_published_payload(ent._coord)
        assert payload["soundSynth"]["volume"] == 30

    def test_range_0_to_60(self):
        ent = make_number("MusicVolume", {})
        assert ent._attr_native_min_value == 0
        assert ent._attr_native_max_value == 60


# ── LightIntensity number ─────────────────────────────────────────────────────

class TestLightIntensity:
    def test_native_value(self):
        ent = make_number("LightIntensity", {"light": {"indicatorBrightness": 75}})
        assert ent.native_value == 75.0

    def test_set_value(self):
        ent = make_number("LightIntensity", {})
        ent.set_native_value(50)
        payload = last_published_payload(ent._coord)
        assert payload["light"]["indicatorBrightness"] == 50

    def test_range_0_to_100(self):
        ent = make_number("LightIntensity", {})
        assert ent._attr_native_min_value == 0
        assert ent._attr_native_max_value == 100
