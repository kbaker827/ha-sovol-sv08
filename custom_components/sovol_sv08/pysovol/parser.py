"""Turn Moonraker responses into the SovolPrinterData model.

Kept free of any I/O so it can be tested with plain dictionaries.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    OPTIONAL_OBJECTS,
    PRINTER_OBJECTS,
    RE_CHAMBER_HEATER,
    RE_EXTRA_FAN,
    RE_FILAMENT_SENSOR,
    RE_LIGHT_LED,
    RE_LIGHT_PIN,
    RE_TEMP_SENSOR,
)
from .models import FileMetadata, FilamentSensor, HeaterData, SovolPrinterData

# Output pins with these names are the stock SV08 LED bar; preferred over
# any other light-like object.
_PREFERRED_LIGHTS = ("output_pin main_led",)


@dataclass(frozen=True)
class DiscoveredObjects:
    """Klipper objects found via /printer/objects/list."""

    filament_sensors: tuple[str, ...] = ()
    temperature_sensors: tuple[str, ...] = ()
    chamber_heaters: tuple[str, ...] = ()
    extra_fans: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    light: str | None = None

    @classmethod
    def from_object_list(cls, objects: list[str]) -> DiscoveredObjects:
        def matching(pattern) -> tuple[str, ...]:
            return tuple(o for o in objects if isinstance(o, str) and pattern.match(o))

        lights = [o for o in objects if o in _PREFERRED_LIGHTS]
        lights += [o for o in matching(RE_LIGHT_PIN) if o not in lights]
        lights += list(matching(RE_LIGHT_LED))
        return cls(
            filament_sensors=matching(RE_FILAMENT_SENSOR),
            temperature_sensors=matching(RE_TEMP_SENSOR),
            chamber_heaters=matching(RE_CHAMBER_HEATER),
            extra_fans=matching(RE_EXTRA_FAN),
            optional=tuple(o for o in OPTIONAL_OBJECTS if o in objects),
            light=lights[0] if lights else None,
        )

    @property
    def has_qgl(self) -> bool:
        return "quad_gantry_level" in self.optional

    @property
    def has_bed_mesh(self) -> bool:
        return "bed_mesh" in self.optional

    @property
    def fan_generics(self) -> tuple[str, ...]:
        """Extra fans that can be set with SET_FAN_SPEED."""
        return tuple(f for f in self.extra_fans if f.startswith("fan_generic "))

    def all_objects(self) -> list[str]:
        """Every object to subscribe to / query."""
        extra = [self.light] if self.light else []
        return (
            PRINTER_OBJECTS
            + list(self.optional)
            + list(self.filament_sensors)
            + list(self.temperature_sensors)
            + list(self.chamber_heaters)
            + list(self.extra_fans)
            + extra
        )


def object_name(object_key: str) -> str:
    """'heater_generic chamber' -> 'chamber'."""
    return object_key.split(" ", 1)[-1]


def object_label(object_key: str) -> str:
    """'temperature_sensor host_temp' -> 'Host Temp'."""
    return object_name(object_key).replace("_", " ").title()


# ---------------------------------------------------------------------------
# Status updates
# ---------------------------------------------------------------------------


def _apply_heater(heater: HeaterData, values: dict[str, Any]) -> None:
    if "temperature" in values and values["temperature"] is not None:
        heater.temperature = round(values["temperature"], 1)
    if "target" in values and values["target"] is not None:
        heater.target = round(values["target"], 1)
    if "power" in values and values["power"] is not None:
        heater.power = values["power"]


def _apply_print_stats(data: SovolPrinterData, ps: dict[str, Any]) -> None:
    stats = data.print_stats
    if "state" in ps:
        stats.state = ps["state"]
    if "filename" in ps:
        stats.filename = ps.get("filename") or ""
    for name in ("total_duration", "print_duration", "filament_used"):
        if name in ps and ps[name] is not None:
            setattr(stats, name, ps[name])
    if "info" in ps:
        info = ps["info"] or {}
        if "current_layer" in info:
            stats.current_layer = info.get("current_layer") or 0
        if "total_layer" in info:
            stats.total_layer = info.get("total_layer") or 0


def _apply_motion(data: SovolPrinterData, status: dict[str, Any]) -> bool:
    changed = False
    if "toolhead" in status:
        th = status["toolhead"]
        for name in ("position", "homed_axes", "max_velocity", "max_accel"):
            if name in th and th[name] is not None:
                setattr(data.toolhead, name, th[name])
        changed = True
    if "gcode_move" in status:
        gm = status["gcode_move"]
        for name in ("speed_factor", "extrude_factor"):
            if name in gm and gm[name] is not None:
                setattr(data.gcode_move, name, gm[name])
        changed = True
    return changed


def _apply_leveling(data: SovolPrinterData, status: dict[str, Any]) -> bool:
    changed = False
    if "quad_gantry_level" in status and "applied" in status["quad_gantry_level"]:
        data.qgl_applied = bool(status["quad_gantry_level"]["applied"])
        changed = True
    if "bed_mesh" in status and "profile_name" in status["bed_mesh"]:
        data.bed_mesh_profile = status["bed_mesh"]["profile_name"] or None
        changed = True
    return changed


def _light_is_on(values: dict[str, Any]) -> bool | None:
    """output_pin reports "value" (0-1); led objects report "color_data"."""
    if "value" in values:
        return (values["value"] or 0) > 0
    color_data = values.get("color_data")
    if color_data:
        return any((v or 0) > 0 for channel in color_data for v in channel)
    return None


def _apply_dynamic(
    data: SovolPrinterData, status: dict[str, Any], objects: DiscoveredObjects
) -> bool:
    changed = False
    for key in objects.filament_sensors:
        if key in status:
            sensor = data.filament_sensors.setdefault(key, FilamentSensor())
            values = status[key]
            if "enabled" in values:
                sensor.enabled = bool(values["enabled"])
            if "filament_detected" in values:
                sensor.filament_detected = bool(values["filament_detected"])
            changed = True
    for key in objects.temperature_sensors:
        if key in status and status[key].get("temperature") is not None:
            data.temperature_sensors[key] = round(status[key]["temperature"], 1)
            changed = True
    for key in objects.chamber_heaters:
        if key in status:
            _apply_heater(data.chamber_heaters.setdefault(key, HeaterData()), status[key])
            changed = True
    for key in objects.extra_fans:
        if key in status and status[key].get("speed") is not None:
            data.extra_fans[key] = round(status[key]["speed"] * 100, 1)
            changed = True
    if objects.light and objects.light in status:
        on = _light_is_on(status[objects.light])
        if on is not None:
            data.light_on = on
            changed = True
    return changed


def apply_status(
    data: SovolPrinterData, status: dict[str, Any], objects: DiscoveredObjects
) -> bool:
    """Merge a (possibly partial) Moonraker status dict into ``data``.

    Returns True when anything the integration tracks was present.
    """
    changed = False

    if "webhooks" in status:
        wh = status["webhooks"]
        if "state" in wh:
            data.klipper_state = wh["state"]
        if "state_message" in wh:
            data.klipper_message = wh["state_message"] or ""
        changed = True

    if "print_stats" in status:
        _apply_print_stats(data, status["print_stats"])
        changed = True

    if "virtual_sdcard" in status:
        vsd = status["virtual_sdcard"]
        for name in ("progress", "is_active", "file_position"):
            if name in vsd and vsd[name] is not None:
                setattr(data.virtual_sdcard, name, vsd[name])
        changed = True

    if "extruder" in status:
        _apply_heater(data.extruder, status["extruder"])
        changed = True

    if "heater_bed" in status:
        _apply_heater(data.heater_bed, status["heater_bed"])
        changed = True

    if "fan" in status and status["fan"].get("speed") is not None:
        data.fan_speed = round(status["fan"]["speed"] * 100, 1)
        changed = True

    if "display_status" in status and "message" in status["display_status"]:
        data.display_message = status["display_status"]["message"] or ""
        changed = True

    if "idle_timeout" in status and "state" in status["idle_timeout"]:
        data.idle_state = status["idle_timeout"]["state"]
        changed = True

    changed |= _apply_motion(data, status)
    changed |= _apply_leveling(data, status)
    changed |= _apply_dynamic(data, status, objects)
    return changed


# ---------------------------------------------------------------------------
# Static config / metadata
# ---------------------------------------------------------------------------


def _apply_limits(heater: HeaterData, section: dict[str, Any]) -> None:
    try:
        if section.get("max_temp"):
            heater.max_temp = float(section["max_temp"])
        if section.get("min_temp") is not None:
            heater.min_temp = max(float(section["min_temp"]), 0.0)
    except (TypeError, ValueError):
        pass


def apply_config_limits(
    data: SovolPrinterData, settings: dict[str, Any], objects: DiscoveredObjects
) -> None:
    """Read heater min/max temperatures from Klipper's ``configfile.settings``.

    Klipper lower-cases section names in ``settings``.
    """
    _apply_limits(data.extruder, settings.get("extruder", {}))
    _apply_limits(data.heater_bed, settings.get("heater_bed", {}))
    for key in objects.chamber_heaters:
        heater = data.chamber_heaters.setdefault(key, HeaterData(max_temp=80.0))
        _apply_limits(heater, settings.get(key.lower(), {}))


def parse_file_metadata(result: dict[str, Any]) -> FileMetadata:
    """Build FileMetadata from a /server/files/metadata result."""

    def number(name: str) -> float | None:
        value = result.get(name)
        return float(value) if isinstance(value, (int, float)) and value > 0 else None

    layers = number("layer_count")
    return FileMetadata(
        estimated_time=number("estimated_time"),
        layer_count=int(layers) if layers else None,
        filament_total=number("filament_total"),
        slicer=result.get("slicer") or None,
    )


def parse_file_list(files: Any) -> list[str]:
    """File names from /server/files/list, newest first.

    Moonraker reports each file's name as "path" (older releases used
    "filename") and doesn't guarantee an order, so sort on "modified".
    """
    if not isinstance(files, list):
        return []
    entries = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        name = entry.get("path") or entry.get("filename")
        if isinstance(name, str) and name:
            modified = entry.get("modified")
            entries.append((modified if isinstance(modified, (int, float)) else 0, name))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [name for _, name in entries]
