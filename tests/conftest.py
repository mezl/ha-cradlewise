"""Pytest configuration — stubs HA + paho so the integration is importable."""
import sys
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

# ── 1. Add repo root to sys.path ─────────────────────────────────────────────
repo_root = Path(__file__).parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))


# ── 2. Stub paho.mqtt ─────────────────────────────────────────────────────────
def _stub_module(name: str) -> ModuleType:
    mod = ModuleType(name)
    sys.modules[name] = mod
    return mod


if "paho" not in sys.modules:
    paho = _stub_module("paho")
    paho_mqtt = _stub_module("paho.mqtt")
    paho_mqtt_client = _stub_module("paho.mqtt.client")
    paho_mqtt_client.Client = MagicMock
    paho.mqtt = paho_mqtt
    paho_mqtt.client = paho_mqtt_client


# ── 3. Stub homeassistant.* ───────────────────────────────────────────────────

class _CallbackDecorator:
    """Stub for @callback decorator."""
    def __call__(self, fn):
        return fn


_callback = _CallbackDecorator()


class _EntityBase:
    """Minimal SensorEntity / SwitchEntity / NumberEntity base."""
    _attr_should_poll = False
    _attr_name: str = ""
    _attr_unique_id: str = ""
    _attr_icon: str = ""
    _attr_device_class = None
    _attr_native_unit_of_measurement = None
    _attr_state_class = None
    _attr_native_min_value: float = 0
    _attr_native_max_value: float = 100
    _attr_native_step: float = 1
    _attr_mode = None
    _attr_translation_key: str = ""

    def __init__(self, coord=None):
        self._coord = coord

    def async_write_ha_state(self):
        pass


class _NumberMode:
    SLIDER = "slider"
    BOX = "box"


class _SensorDeviceClass:
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    BATTERY = "battery"
    SOUND_PRESSURE = "sound_pressure"


class _SensorStateClass:
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class _BinarySensorDeviceClass:
    CONNECTIVITY = "connectivity"
    OCCUPANCY = "occupancy"
    SOUND = "sound"


class _DeviceInfo:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# homeassistant base
ha_mod = _stub_module("homeassistant")

ha_core = _stub_module("homeassistant.core")
ha_core.HomeAssistant = MagicMock
ha_core.callback = _callback

ha_cfg = _stub_module("homeassistant.config_entries")
ha_cfg.ConfigEntry = MagicMock

ha_const = _stub_module("homeassistant.const")
ha_const.PERCENTAGE = "%"

class _UnitOfTemperature:
    CELSIUS = "°C"
    FAHRENHEIT = "°F"

ha_const.UnitOfTemperature = _UnitOfTemperature

ha_helpers = _stub_module("homeassistant.helpers")
ha_helpers_dev = _stub_module("homeassistant.helpers.device_registry")
ha_helpers_dev.DeviceInfo = _DeviceInfo
ha_helpers_ep = _stub_module("homeassistant.helpers.entity_platform")
ha_helpers_ep.AddEntitiesCallback = MagicMock
ha_helpers_ent = _stub_module("homeassistant.helpers.entity")
ha_helpers_ep.async_get_current_platform = MagicMock

ha_components = _stub_module("homeassistant.components")

# sensor
ha_sensor = _stub_module("homeassistant.components.sensor")
ha_sensor.SensorEntity = _EntityBase
ha_sensor.SensorStateClass = _SensorStateClass
ha_sensor.SensorDeviceClass = _SensorDeviceClass

# switch
ha_switch = _stub_module("homeassistant.components.switch")
ha_switch.SwitchEntity = _EntityBase

# number
ha_number = _stub_module("homeassistant.components.number")
ha_number.NumberEntity = _EntityBase
ha_number.NumberMode = _NumberMode

# binary_sensor
ha_binary = _stub_module("homeassistant.components.binary_sensor")
ha_binary.BinarySensorEntity = _EntityBase
ha_binary.BinarySensorDeviceClass = _BinarySensorDeviceClass

# select
ha_select = _stub_module("homeassistant.components.select")
ha_select.SelectEntity = _EntityBase

# camera
ha_camera = _stub_module("homeassistant.components.camera")
ha_camera.Camera = _EntityBase

# ssl (needed by coordinator)
import ssl as _ssl_real
sys.modules.setdefault("ssl", _ssl_real)
