"""Unit tests for CradlewiseCoordinator state helpers and command API."""
import json
import pytest
from unittest.mock import MagicMock, patch, call


class FakeHass:
    """Minimal hass stub."""
    def add_job(self, cb):
        cb()


def make_coordinator():
    """Return a coordinator with a connected mock MQTT client."""
    import sys, types
    # Stub paho.mqtt so we can import coordinator without actual MQTT
    paho = types.ModuleType("paho")
    paho_mqtt = types.ModuleType("paho.mqtt")
    paho_mqtt_client = types.ModuleType("paho.mqtt.client")
    paho_mqtt_client.Client = MagicMock
    sys.modules.setdefault("paho", paho)
    sys.modules.setdefault("paho.mqtt", paho_mqtt)
    sys.modules.setdefault("paho.mqtt.client", paho_mqtt_client)

    import importlib, os, pathlib
    coord_path = pathlib.Path(__file__).parent.parent / "custom_components" / "cradlewise"
    sys.path.insert(0, str(coord_path.parent.parent))

    # Patch const before importing
    with patch.dict("sys.modules"):
        from custom_components.cradlewise.coordinator import CradlewiseCoordinator  # noqa: PLC0415

    coord = CradlewiseCoordinator.__new__(CradlewiseCoordinator)
    coord.hass = FakeHass()
    coord.state = {}
    coord.available = True
    coord._listeners = []
    coord._client = MagicMock()
    return coord


# ── Coordinator import ────────────────────────────────────────────────────────

def _get_coord():
    """Import coordinator lazily with patched mqtt."""
    import sys, types
    if "paho.mqtt.client" not in sys.modules:
        paho = types.ModuleType("paho")
        paho_mqtt = types.ModuleType("paho.mqtt")
        paho_mqtt_client = types.ModuleType("paho.mqtt.client")
        paho_mqtt_client.Client = MagicMock
        sys.modules["paho"] = paho
        sys.modules["paho.mqtt"] = paho_mqtt
        sys.modules["paho.mqtt.client"] = paho_mqtt_client

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from custom_components.cradlewise.coordinator import CradlewiseCoordinator
    return CradlewiseCoordinator


# ── State accessor tests ──────────────────────────────────────────────────────

class TestStateAccessors:
    def setup_method(self):
        Coord = _get_coord()
        self.coord = Coord.__new__(Coord)
        self.coord.hass = FakeHass()
        self.coord.state = {}
        self.coord.available = True
        self.coord._listeners = []
        self.coord._client = MagicMock()

    def test_get_actuator_empty(self):
        assert self.coord.get_actuator() == {}

    def test_get_actuator_present(self):
        self.coord.state = {"actuator": {"on": True, "bounceAlwaysOnIntensity": 3}}
        assert self.coord.get_actuator()["on"] is True
        assert self.coord.get_actuator()["bounceAlwaysOnIntensity"] == 3

    def test_get_sound_empty(self):
        assert self.coord.get_sound() == {}

    def test_get_sound_present(self):
        self.coord.state = {"soundSynth": {"play": True, "volume": 40, "mood": "whitenoise"}}
        assert self.coord.get_sound()["play"] is True
        assert self.coord.get_sound()["volume"] == 40

    def test_get_light_empty(self):
        assert self.coord.get_light() == {}

    def test_get_light_present(self):
        self.coord.state = {"light": {"indicatorBrightness": 100}}
        assert self.coord.get_light()["indicatorBrightness"] == 100

    def test_get_actuator_returns_empty_on_none(self):
        self.coord.state = {"actuator": None}
        assert self.coord.get_actuator() == {}


class TestStateMerge:
    def setup_method(self):
        Coord = _get_coord()
        self.coord = Coord.__new__(Coord)
        self.coord.hass = FakeHass()
        self.coord.state = {}
        self.coord.available = False
        self.coord._listeners = []
        self.coord._client = MagicMock()

    def test_merge_flat_keys(self):
        self.coord._merge({"babyPresent": True, "mode": "smart"})
        assert self.coord.state["babyPresent"] is True
        assert self.coord.state["mode"] == "smart"

    def test_merge_nested_dict_deep_merge(self):
        self.coord.state = {"actuator": {"on": False, "bounceAlwaysOnIntensity": 2}}
        self.coord._merge({"actuator": {"on": True}})
        assert self.coord.state["actuator"]["on"] is True
        assert self.coord.state["actuator"]["bounceAlwaysOnIntensity"] == 2

    def test_merge_overwrites_scalar(self):
        self.coord.state = {"mode": "smart"}
        self.coord._merge({"mode": "manual"})
        assert self.coord.state["mode"] == "manual"

    def test_merge_nested_dict_replaces_scalar(self):
        self.coord.state = {"actuator": 42}
        self.coord._merge({"actuator": {"on": True}})
        assert self.coord.state["actuator"] == {"on": True}


class TestSendDesired:
    def setup_method(self):
        Coord = _get_coord()
        self.coord = Coord.__new__(Coord)
        self.coord.hass = FakeHass()
        self.coord.state = {}
        self.coord.available = True
        self.coord._listeners = []
        self.coord._client = MagicMock()

    def test_send_desired_publishes_correct_payload(self):
        from custom_components.cradlewise.const import SHADOW_UPDATE  # noqa: PLC0415
        self.coord.send_desired({"actuator": {"on": True}})
        published = self.coord._client.publish.call_args
        topic = published[0][0]
        payload = json.loads(published[0][1])
        assert topic == SHADOW_UPDATE
        assert payload["state"]["desired"] == {"actuator": {"on": True}}

    def test_send_desired_noop_without_client(self):
        self.coord._client = None
        # Should not raise
        self.coord.send_desired({"actuator": {"on": True}})

    def test_send_desired_rocking_on(self):
        self.coord.send_desired({"actuator": {"on": True, "bounceAlwaysOn": False}})
        payload = json.loads(self.coord._client.publish.call_args[0][1])
        assert payload["state"]["desired"]["actuator"]["on"] is True

    def test_send_desired_music_off(self):
        self.coord.send_desired({"soundSynth": {"play": False}})
        payload = json.loads(self.coord._client.publish.call_args[0][1])
        assert payload["state"]["desired"]["soundSynth"]["play"] is False


class TestListeners:
    def setup_method(self):
        Coord = _get_coord()
        self.coord = Coord.__new__(Coord)
        self.coord.hass = FakeHass()
        self.coord.state = {}
        self.coord.available = True
        self.coord._listeners = []
        self.coord._client = MagicMock()

    def test_add_and_remove_listener(self):
        calls = []
        unsub = self.coord.async_add_listener(lambda: calls.append(1))
        self.coord._notify()
        assert len(calls) == 1
        unsub()
        self.coord._notify()
        assert len(calls) == 1  # no new call after unsub

    def test_multiple_listeners(self):
        a, b = [], []
        self.coord.async_add_listener(lambda: a.append(1))
        self.coord.async_add_listener(lambda: b.append(1))
        self.coord._notify()
        assert len(a) == 1
        assert len(b) == 1


class TestShadowMessageHandling:
    def setup_method(self):
        Coord = _get_coord()
        self.coord = Coord.__new__(Coord)
        self.coord.hass = FakeHass()
        self.coord.state = {}
        self.coord.available = False
        self.coord._listeners = []
        self.coord._client = MagicMock()

    def _make_msg(self, topic, payload):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = json.dumps(payload).encode()
        return msg

    def test_on_get_accepted_merges_reported(self):
        from custom_components.cradlewise.const import SHADOW_GET_ACC  # noqa: PLC0415
        msg = self._make_msg(SHADOW_GET_ACC, {
            "state": {"reported": {"babyPresent": True, "actuator": {"on": False}}}
        })
        self.coord._on_message(None, None, msg)
        assert self.coord.state["babyPresent"] is True
        assert self.coord.available is True

    def test_on_update_accepted_merges_reported(self):
        from custom_components.cradlewise.const import SHADOW_UPD_ACC  # noqa: PLC0415
        msg = self._make_msg(SHADOW_UPD_ACC, {
            "state": {"reported": {"actuator": {"on": True}}}
        })
        self.coord._on_message(None, None, msg)
        assert self.coord.state["actuator"]["on"] is True

    def test_on_delta_ignored_for_state(self):
        from custom_components.cradlewise.const import SHADOW_DELTA  # noqa: PLC0415
        self.coord.state = {"actuator": {"on": False}}
        msg = self._make_msg(SHADOW_DELTA, {
            "state": {"actuator": {"on": True}}
        })
        self.coord._on_message(None, None, msg)
        # Delta should NOT update self.state
        assert self.coord.state["actuator"]["on"] is False

    def test_disconnect_sets_unavailable(self):
        self.coord.available = True
        self.coord._on_disconnect(None, None, 0)
        assert self.coord.available is False
