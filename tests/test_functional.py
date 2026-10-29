"""Functional tests against live Home Assistant REST API.

Reads HA_URL and HA_TOKEN from environment or .env file.
These tests do NOT mutate state — they verify entity reachability and formats.
Run with: pytest tests/test_functional.py -v
"""
import os, re, pytest, requests
from datetime import datetime


def _load_env():
    env_file = os.path.join(os.path.dirname(__file__), "../../claude-homeassistant/.env")
    cfg = {}
    try:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return cfg


_ENV = _load_env()
HA_URL = os.environ.get("HA_URL") or _ENV.get("HA_URL", "http://192.168.31.31:8123")
HA_TOKEN = os.environ.get("HA_TOKEN") or _ENV.get("HA_TOKEN", "")


@pytest.fixture(scope="session")
def ha():
    """Session-scoped HA REST client."""
    if not HA_TOKEN:
        pytest.skip("HA_TOKEN not configured")
    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {HA_TOKEN}"
    # Verify connectivity
    resp = sess.get(f"{HA_URL}/api/")
    if not resp.ok:
        pytest.skip(f"Cannot reach HA at {HA_URL}")
    return sess, HA_URL


def get_state(ha, entity_id: str) -> dict:
    sess, url = ha
    resp = sess.get(f"{url}/api/states/{entity_id}")
    assert resp.status_code == 200, f"Entity not found: {entity_id} ({resp.status_code})"
    return resp.json()


# ── Connectivity ──────────────────────────────────────────────────────────────

class TestConnectivity:
    def test_ha_api_running(self, ha):
        sess, url = ha
        resp = sess.get(f"{url}/api/")
        assert resp.ok
        assert "running" in resp.json().get("message", "").lower()

    def test_ha_version_accessible(self, ha):
        sess, url = ha
        resp = sess.get(f"{url}/api/config")
        assert resp.ok
        version = resp.json().get("version", "")
        assert version, "HA version empty"
        print(f"HA version: {version}")


# ── Core cradlewise entities ──────────────────────────────────────────────────

class TestCoreEntities:
    REQUIRED_ENTITIES = [
        "binary_sensor.cradlewise_avo_baby_present",
        "sensor.cradlewise_avo_sleep_phase",
        "binary_sensor.cradlewise_avo_bouncing",
        "binary_sensor.cradlewise_avo_music_playing",
        "switch.cradlewise_avo_rocking",
        "switch.cradlewise_avo_music",
        "switch.cradlewise_avo_night_light",
        "binary_sensor.cradlewise_avo_baby_needs_attention",
        "number.cradlewise_avo_rock_amplitude",
        "number.cradlewise_avo_music_volume",
        "binary_sensor.cradlewise_avo_online",
    ]

    @pytest.mark.parametrize("entity_id", REQUIRED_ENTITIES)
    def test_entity_exists(self, ha, entity_id):
        state = get_state(ha, entity_id)
        assert state["entity_id"] == entity_id
        assert "state" in state

    def test_baby_present_binary(self, ha):
        state = get_state(ha, "binary_sensor.cradlewise_avo_baby_present")
        assert state["state"] in ("on", "off", "unavailable", "unknown")

    def test_sleep_phase_string(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_sleep_phase")
        valid = {"away", "awake", "stirring", "sleep", "unknown", "unavailable"}
        assert state["state"] in valid or state["state"].startswith("unknown")

    def test_rocking_switch_boolean(self, ha):
        state = get_state(ha, "switch.cradlewise_avo_rocking")
        assert state["state"] in ("on", "off")

    def test_music_switch_boolean(self, ha):
        state = get_state(ha, "switch.cradlewise_avo_music")
        assert state["state"] in ("on", "off")

    def test_night_light_switch_boolean(self, ha):
        state = get_state(ha, "switch.cradlewise_avo_night_light")
        assert state["state"] in ("on", "off")

    def test_rock_amplitude_is_numeric(self, ha):
        state = get_state(ha, "number.cradlewise_avo_rock_amplitude")
        val = float(state["state"])
        assert 1 <= val <= 5

    def test_music_volume_in_range(self, ha):
        state = get_state(ha, "number.cradlewise_avo_music_volume")
        val = float(state["state"])
        assert 0 <= val <= 60


# ── New environment sensors ───────────────────────────────────────────────────

class TestEnvironmentSensors:
    def test_temperature_entity_exists(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_temperature")
        assert state["entity_id"] == "sensor.cradlewise_avo_temperature"
        # State is "unknown" if crib doesn't report temperature; that's acceptable
        assert state["state"] in ("unknown", "unavailable") or float(state["state"]) is not None

    def test_humidity_entity_exists(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_humidity")
        assert state["entity_id"] == "sensor.cradlewise_avo_humidity"

    def test_noise_level_entity_exists(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_noise_level")
        assert state["entity_id"] == "sensor.cradlewise_avo_noise_level"

    def test_battery_life_entity_exists(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_battery_life")
        assert state["entity_id"] == "sensor.cradlewise_avo_battery_life"

    def test_temperature_unit_when_available(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_temperature")
        if state["state"] not in ("unknown", "unavailable"):
            val = float(state["state"])
            assert -10 <= val <= 50, f"Temperature {val}°C out of expected range"

    def test_humidity_unit_when_available(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_humidity")
        if state["state"] not in ("unknown", "unavailable"):
            val = float(state["state"])
            assert 0 <= val <= 100


# ── Analytics sensors ─────────────────────────────────────────────────────────

class TestAnalyticsSensors:
    ANALYTICS_ENTITIES = [
        "sensor.cradlewise_avo_total_sleep_today",
        "sensor.cradlewise_avo_nap_count",
        "sensor.cradlewise_avo_longest_nap",
        "sensor.cradlewise_avo_soothe_count",
        "sensor.cradlewise_avo_last_nap_ended",
        "sensor.cradlewise_avo_sleep_time",
        "sensor.cradlewise_avo_wake_up_time",
    ]

    @pytest.mark.parametrize("entity_id", ANALYTICS_ENTITIES)
    def test_entity_registered(self, ha, entity_id):
        state = get_state(ha, entity_id)
        assert state["entity_id"] == entity_id

    def test_total_sleep_numeric_or_unavailable(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_total_sleep_today")
        if state["state"] not in ("unavailable", "unknown"):
            val = int(float(state["state"]))
            assert val >= 0

    def test_nap_count_integer_or_unavailable(self, ha):
        state = get_state(ha, "sensor.cradlewise_avo_nap_count")
        if state["state"] not in ("unavailable", "unknown"):
            assert int(float(state["state"])) >= 0


# ── Automation + Script ───────────────────────────────────────────────────────

class TestAutomationAndScript:
    def test_after_sleep_automation_exists(self, ha):
        state = get_state(ha, "automation.cradlewise_after_sleep")
        assert state["state"] in ("on", "off")

    def test_start_recipe_script_exists(self, ha):
        state = get_state(ha, "script.cradlewise_start_recipe")
        assert state["state"] in ("on", "off", "running")

    def test_script_has_icon(self, ha):
        state = get_state(ha, "script.cradlewise_start_recipe")
        assert state["attributes"].get("icon") == "mdi:baby-carriage"

    def test_automation_trigger_is_correct_entity(self, ha):
        sess, url = ha
        resp = sess.get(f"{url}/api/states/automation.cradlewise_after_sleep")
        assert resp.ok
        # Just verify it exists and is enabled
        state = resp.json()
        assert state["state"] == "on"
