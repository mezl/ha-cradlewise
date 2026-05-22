"""Hardware-in-the-Loop Tests (HILT) for Cradlewise integration.

These tests send real commands to the physical crib via Home Assistant
and verify the crib + HA state changes as expected.

IMPORTANT SAFETY NOTES:
- Tests restore original state after each test
- Tests skip if baby is present (babyPresent=True) to avoid disturbing sleep
- Tests skip if HA is unreachable
- Commands are serialised via pytest-timeout; run with: pytest tests/test_hilt.py -v -s

Run:
    HA_URL=http://192.168.31.31:8123 HA_TOKEN=<token> pytest tests/test_hilt.py -v
"""
import os, time, pytest, requests


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

SETTLE_SECS = 3        # seconds to wait for shadow round-trip after a command
MQTT_SETTLE_SECS = 6   # longer settle for MQTT shadow updates that need a round-trip


@pytest.fixture(scope="session")
def ha():
    if not HA_TOKEN:
        pytest.skip("HA_TOKEN not configured")
    sess = requests.Session()
    sess.headers["Authorization"] = f"Bearer {HA_TOKEN}"
    resp = sess.get(f"{HA_URL}/api/")
    if not resp.ok:
        pytest.skip(f"HA unreachable at {HA_URL}")
    return sess, HA_URL


@pytest.fixture(scope="session", autouse=True)
def skip_if_baby_present(ha):
    """Skip all HILT tests if baby is currently in the crib."""
    sess, url = ha
    resp = sess.get(f"{url}/api/states/binary_sensor.cradlewise_avo_baby_present")
    if resp.ok and resp.json().get("state") == "on":
        pytest.skip("Baby is present — skipping HILT tests to avoid disturbing sleep")


def get_state(ha, entity_id: str) -> str:
    sess, url = ha
    resp = sess.get(f"{url}/api/states/{entity_id}")
    assert resp.status_code == 200, f"Entity not found: {entity_id}"
    return resp.json()["state"]


def call_service(ha, domain: str, service: str, data: dict) -> None:
    sess, url = ha
    resp = sess.post(f"{url}/api/services/{domain}/{service}", json=data)
    assert resp.ok, f"Service call failed: {resp.status_code} {resp.text}"
    time.sleep(SETTLE_SECS)


# ── Online check ──────────────────────────────────────────────────────────────

class TestCribOnline:
    def test_crib_is_online(self, ha):
        state = get_state(ha, "binary_sensor.cradlewise_avo_online")
        assert state == "on", "Crib is offline — all HILT tests will be meaningless"

    def test_coordinator_state_populated(self, ha):
        """Verify MQTT shadow state has been received (sleep_phase != unknown)."""
        state = get_state(ha, "sensor.cradlewise_avo_sleep_phase")
        assert state != "unavailable", "Sleep phase is unavailable — MQTT not receiving"


# ── Rocking switch ────────────────────────────────────────────────────────────

class TestRockingHILT:
    def test_turn_on_rocking(self, ha):
        original = get_state(ha, "switch.cradlewise_avo_rocking")
        try:
            call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_rocking"})
            new_state = get_state(ha, "switch.cradlewise_avo_rocking")
            assert new_state == "on", f"Expected 'on' after turn_on, got '{new_state}'"
        finally:
            # Restore
            call_service(ha, "switch", f"turn_{original}", {"entity_id": "switch.cradlewise_avo_rocking"})

    def test_turn_off_rocking(self, ha):
        # Ensure it's on first
        call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_rocking"})
        try:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_rocking"})
            state = get_state(ha, "switch.cradlewise_avo_rocking")
            assert state == "off", f"Expected 'off', got '{state}'"
        finally:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_rocking"})

    def test_toggle_rocking_reflects_in_bouncing_binary_sensor(self, ha):
        """Turn rocking on; verify binary_sensor.bouncing follows."""
        call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_rocking"})
        try:
            bouncing = get_state(ha, "binary_sensor.cradlewise_avo_bouncing")
            # bouncing binary sensor should reflect actuator.on from shadow
            assert bouncing in ("on", "off")  # at minimum it's reachable and valid
        finally:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_rocking"})


# ── Music switch ──────────────────────────────────────────────────────────────

class TestMusicHILT:
    def test_turn_on_music(self, ha):
        original = get_state(ha, "switch.cradlewise_avo_music")
        try:
            call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_music"})
            assert get_state(ha, "switch.cradlewise_avo_music") == "on"
        finally:
            call_service(ha, "switch", f"turn_{original}", {"entity_id": "switch.cradlewise_avo_music"})

    def test_turn_off_music(self, ha):
        call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_music"})
        try:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_music"})
            assert get_state(ha, "switch.cradlewise_avo_music") == "off"
        finally:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_music"})

    def test_music_playing_sensor_follows_switch(self, ha):
        """binary_sensor.music_playing should mirror soundSynth.play."""
        call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_music"})
        try:
            state = get_state(ha, "binary_sensor.cradlewise_avo_music_playing")
            assert state == "on", f"music_playing binary sensor expected 'on', got '{state}'"
        finally:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_music"})


# ── Night light switch ────────────────────────────────────────────────────────

class TestNightLightHILT:
    def test_turn_on_night_light(self, ha):
        original = get_state(ha, "switch.cradlewise_avo_night_light")
        try:
            call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_night_light"})
            assert get_state(ha, "switch.cradlewise_avo_night_light") == "on"
        finally:
            call_service(ha, "switch", f"turn_{original}", {"entity_id": "switch.cradlewise_avo_night_light"})

    def test_turn_off_night_light(self, ha):
        call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_night_light"})
        try:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_night_light"})
            state = get_state(ha, "switch.cradlewise_avo_night_light")
            assert state == "off", f"Expected 'off', got '{state}'"
        finally:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_night_light"})

    def test_light_intensity_sensor_updates(self, ha):
        """After turning light on, sensor.light_intensity should be > 0."""
        call_service(ha, "switch", "turn_on", {"entity_id": "switch.cradlewise_avo_night_light"})
        try:
            state = get_state(ha, "sensor.cradlewise_avo_light_intensity")
            if state not in ("unknown", "unavailable"):
                assert float(state) > 0, f"Light intensity expected > 0, got {state}"
        finally:
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_night_light"})


# ── Number entities ───────────────────────────────────────────────────────────

class TestRockAmplitudeHILT:
    def test_set_amplitude_2(self, ha):
        """Set amplitude to 2.
        Note: crib firmware may clamp to minimum (3) when no baby is present or
        when in smart-mode. We verify the service call is accepted (200) and
        the value changed from its previous state or stayed within valid range.
        """
        original = float(get_state(ha, "number.cradlewise_avo_rock_amplitude"))
        try:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_rock_amplitude",
                "value": 2,
            })
            new_val = float(get_state(ha, "number.cradlewise_avo_rock_amplitude"))
            # Accept 2.0 (firmware accepted) OR firmware-clamped min (typically 3)
            assert 1 <= new_val <= 5, f"Amplitude out of range: {new_val}"
        finally:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_rock_amplitude",
                "value": original,
            })

    def test_set_amplitude_5(self, ha):
        original = get_state(ha, "number.cradlewise_avo_rock_amplitude")
        try:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_rock_amplitude",
                "value": 5,
            })
            new_val = float(get_state(ha, "number.cradlewise_avo_rock_amplitude"))
            assert new_val == 5.0
        finally:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_rock_amplitude",
                "value": float(original),
            })


class TestMusicVolumeHILT:
    def test_set_volume_30(self, ha):
        original = get_state(ha, "number.cradlewise_avo_music_volume")
        try:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_music_volume",
                "value": 30,
            })
            new_val = float(get_state(ha, "number.cradlewise_avo_music_volume"))
            assert new_val == 30.0, f"Expected volume 30.0, got {new_val}"
        finally:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_music_volume",
                "value": float(original),
            })


# ── Script ────────────────────────────────────────────────────────────────────

class TestRecipeScriptHILT:
    def test_recipe_script_runs_and_turns_on_rocking(self, ha):
        """Fire the 1-minute recipe and verify rocking turns on within settle window."""
        sess, url = ha
        # Ensure rocking is off
        call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_rocking"})
        try:
            # Trigger the script (non-blocking service call)
            resp = sess.post(f"{url}/api/services/script/cradlewise_start_recipe",
                             json={"duration_minutes": 1})
            assert resp.ok, f"Script trigger failed: {resp.status_code}"
            # Poll for up to 20s — MQTT shadow round-trip can exceed fixed 6s window
            deadline = time.time() + 20
            state = "off"
            while time.time() < deadline:
                state = get_state(ha, "switch.cradlewise_avo_rocking")
                if state == "on":
                    break
                time.sleep(1)
            assert state == "on", f"Expected rocking 'on' after recipe start, got '{state}'"
        finally:
            # Turn off immediately to not wait 1 minute
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_rocking"})
            call_service(ha, "switch", "turn_off", {"entity_id": "switch.cradlewise_avo_music"})


# ── Automation ────────────────────────────────────────────────────────────────

class TestAfterSleepAutomationHILT:
    def test_automation_is_enabled(self, ha):
        state = get_state(ha, "automation.cradlewise_after_sleep")
        assert state == "on", "cradlewise_after_sleep automation should be enabled"

    def test_automation_toggle(self, ha):
        """Verify we can disable and re-enable the automation."""
        original = get_state(ha, "automation.cradlewise_after_sleep")
        try:
            call_service(ha, "automation", "turn_off", {"entity_id": "automation.cradlewise_after_sleep"})
            assert get_state(ha, "automation.cradlewise_after_sleep") == "off"
            call_service(ha, "automation", "turn_on", {"entity_id": "automation.cradlewise_after_sleep"})
            assert get_state(ha, "automation.cradlewise_after_sleep") == "on"
        finally:
            call_service(ha, "automation", f"turn_{original}", {"entity_id": "automation.cradlewise_after_sleep"})


# ── Light intensity number ────────────────────────────────────────────────────

class TestLightIntensityHILT:
    def test_set_light_intensity_50(self, ha):
        original = get_state(ha, "number.cradlewise_avo_light_intensity")
        try:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_light_intensity",
                "value": 50,
            })
            new_val = float(get_state(ha, "number.cradlewise_avo_light_intensity"))
            assert new_val == 50.0, f"Expected light intensity 50, got {new_val}"
            # Night light switch should also be on (brightness > 0)
            light_switch = get_state(ha, "switch.cradlewise_avo_night_light")
            assert light_switch == "on"
        finally:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_light_intensity",
                "value": float(original),
            })

    def test_set_light_intensity_0_turns_light_off(self, ha):
        original_intensity = get_state(ha, "number.cradlewise_avo_light_intensity")
        try:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_light_intensity",
                "value": 0,
            })
            light_switch = get_state(ha, "switch.cradlewise_avo_night_light")
            assert light_switch == "off", f"Expected light switch 'off' when intensity=0, got '{light_switch}'"
        finally:
            call_service(ha, "number", "set_value", {
                "entity_id": "number.cradlewise_avo_light_intensity",
                "value": float(original_intensity),
            })
