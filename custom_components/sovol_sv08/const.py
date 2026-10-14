"""Constants for the Sovol SV08 Home Assistant integration."""

DOMAIN = "sovol_sv08"
MANUFACTURER = "Sovol"
MODEL = "SV08"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_TEMPERATURE_UNIT = "temperature_unit"

# Unit used for every printer temperature. 3D printing works in °C, so that is
# the default even when Home Assistant itself is set to °F.
TEMP_UNIT_CELSIUS = "celsius"
TEMP_UNIT_FAHRENHEIT = "fahrenheit"
TEMP_UNIT_SYSTEM = "system"  # follow Home Assistant's unit system
DEFAULT_TEMPERATURE_UNIT = TEMP_UNIT_CELSIUS

# The SV08 serves Mainsail and proxies Moonraker through nginx on port 80.
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30  # seconds
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 300
