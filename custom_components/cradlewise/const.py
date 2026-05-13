"""Constants for the Cradlewise HA integration."""

DOMAIN = "cradlewise"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_LOCAL_HOST = "local_host"  # LAN IP of the crib (e.g. 192.168.1.50)

# Polling intervals
DEFAULT_SCAN_INTERVAL = 30  # seconds (REST-only fallback)
MQTT_SCAN_INTERVAL = 300  # seconds (when MQTT is active)

PLATFORMS: list[str] = ["sensor", "binary_sensor", "camera"]
