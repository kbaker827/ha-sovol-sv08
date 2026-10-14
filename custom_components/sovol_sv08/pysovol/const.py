"""Constants for the pysovol Moonraker API client."""
import re

# Moonraker HTTP endpoints
ENDPOINT_PRINTER_INFO = "/printer/info"
ENDPOINT_SERVER_INFO = "/server/info"
ENDPOINT_PRINTER_OBJECTS_QUERY = "/printer/objects/query"
ENDPOINT_PRINTER_OBJECTS_LIST = "/printer/objects/list"
ENDPOINT_PRINT_PAUSE = "/printer/print/pause"
ENDPOINT_PRINT_RESUME = "/printer/print/resume"
ENDPOINT_PRINT_CANCEL = "/printer/print/cancel"
ENDPOINT_PRINT_START = "/printer/print/start"
ENDPOINT_EMERGENCY_STOP = "/printer/emergency_stop"
ENDPOINT_PRINTER_RESTART = "/printer/restart"
ENDPOINT_FIRMWARE_RESTART = "/printer/firmware_restart"
ENDPOINT_GCODE_SCRIPT = "/printer/gcode/script"
ENDPOINT_FILES_LIST = "/server/files/list"
ENDPOINT_FILES_METADATA = "/server/files/metadata"
ENDPOINT_WEBCAMS_LIST = "/server/webcams/list"

# HA event names fired on print-state transitions
EVENT_PRINT_STARTED = "sovol_sv08_print_started"
EVENT_PRINT_COMPLETE = "sovol_sv08_print_complete"
EVENT_PRINT_FAILED = "sovol_sv08_print_failed"
EVENT_PRINT_PAUSED = "sovol_sv08_print_paused"
EVENT_PRINT_CANCELLED = "sovol_sv08_print_cancelled"

# Map Moonraker print state -> HA event (only states that trigger events)
PRINT_STATE_EVENTS: dict[str, str] = {
    "printing": EVENT_PRINT_STARTED,
    "complete": EVENT_PRINT_COMPLETE,
    "error": EVENT_PRINT_FAILED,
    "paused": EVENT_PRINT_PAUSED,
    "cancelled": EVENT_PRINT_CANCELLED,
}

# WebSocket
WS_ENDPOINT = "/websocket"
WS_METHOD_SUBSCRIBE = "printer.objects.subscribe"
WS_NOTIFY_STATUS_UPDATE = "notify_status_update"
WS_NOTIFY_KLIPPY_READY = "notify_klippy_ready"
WS_NOTIFY_KLIPPY_SHUTDOWN = "notify_klippy_shutdown"
WS_NOTIFY_KLIPPY_DISCONNECTED = "notify_klippy_disconnected"
WS_NOTIFY_HISTORY_CHANGED = "notify_history_changed"
WS_NOTIFY_FILELIST_CHANGED = "notify_filelist_changed"

# Camera. The SV08 ships with a USB camera served by crowsnest/ustreamer,
# which answers on the mjpg-streamer style "?action=" URLs.
CAMERA_STREAM_PATH = "/webcam/?action=stream"
CAMERA_SNAPSHOT_PATH = "/webcam/?action=snapshot"

# Printer objects every Klipper printer has; subscribed with all fields.
PRINTER_OBJECTS = [
    "print_stats",
    "virtual_sdcard",
    "extruder",
    "heater_bed",
    "toolhead",
    "display_status",
    "webhooks",
    "idle_timeout",
    "gcode_move",
]

# Objects that only some printers have, subscribed only when present.
# A stock SV08 has no "fan": its part cooling is fan_generic fan0 + fan1.
# "probe" is the inductive probe, or the eddy probe's alias.
OPTIONAL_OBJECTS = ["fan", "quad_gantry_level", "bed_mesh", "probe"]
# Sovol's Z_OFFSET_CALIBRATION command (stock SV08 and SV08 Max)
Z_OFFSET_CALIBRATION_OBJECT = "z_offset_calibration"

# Regexes for dynamically discovered Klipper objects
RE_EDDY_PROBE = re.compile(r"^probe_eddy_current\s+\S+")
# fan_generic fans that cool the part, used when there's no [fan] section.
# The SV08 drives its part cooling from fan0 (back) and fan1 (front).
RE_PART_FAN = re.compile(r"^fan_generic\s+(fan[01]|\S*(part|model|cool)\S*)$", re.IGNORECASE)
RE_FILAMENT_SENSOR = re.compile(r"^filament_(switch|motion)_sensor\s+\S+")
RE_TEMP_SENSOR = re.compile(r"^temperature_sensor\s+\S+")
RE_CHAMBER_HEATER = re.compile(r"^heater_generic\s+\S+")
# Fans other than the part-cooling fan (hotend, controller, exhaust, ...)
RE_EXTRA_FAN = re.compile(r"^(heater_fan|controller_fan|fan_generic|temperature_fan)\s+\S+")
# Lights. The stock SV08 drives its LED bar from "output_pin main_led";
# modded printers often use a neopixel/led strip instead.
RE_LIGHT_PIN = re.compile(r"^output_pin\s+\S*(led|light|lamp)\S*$", re.IGNORECASE)
RE_LIGHT_LED = re.compile(r"^(led|neopixel|dotstar|pca9533|pca9632)\s+\S+")

# Print states (Moonraker/Klipper)
STATE_STANDBY = "standby"
STATE_PRINTING = "printing"
STATE_PAUSED = "paused"
STATE_COMPLETE = "complete"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

# Klipper states
KLIPPER_READY = "ready"
KLIPPER_STARTUP = "startup"
KLIPPER_SHUTDOWN = "shutdown"
KLIPPER_ERROR = "error"

# SV08 stock limits, used until the real ones are read from the Klipper config
DEFAULT_EXTRUDER_MAX_TEMP = 300.0
DEFAULT_BED_MAX_TEMP = 100.0

# Below this progress the file-position estimate is too noisy, so the
# slicer's estimate is used for time remaining when the file has one.
SLICER_ESTIMATE_PROGRESS_LIMIT = 0.1
