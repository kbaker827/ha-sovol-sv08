"""Tests for pysovol.parser (no network)."""
from __future__ import annotations

import copy

from fake_moonraker import SV08_OBJECTS, SV08_SETTINGS, SV08_STATUS
from pysovol.models import SovolPrinterData
from pysovol.parser import (
    DiscoveredObjects,
    apply_config_limits,
    apply_status,
    object_label,
    parse_file_list,
    parse_file_metadata,
)


def sv08_objects() -> DiscoveredObjects:
    return DiscoveredObjects.from_object_list(SV08_OBJECTS)


class TestDiscovery:
    def test_finds_sv08_objects(self):
        objects = sv08_objects()

        assert objects.filament_sensors == ("filament_switch_sensor filament_sensor",)
        assert objects.temperature_sensors == (
            "temperature_sensor mcu_temp",
            "temperature_sensor host_temp",
        )
        assert objects.extra_fans == (
            "heater_fan hotend_fan",
            "controller_fan controller_fan",
            "fan_generic exhaust_fan",
        )
        assert objects.fan_generics == ("fan_generic exhaust_fan",)
        assert objects.light == "output_pin main_led"
        assert objects.has_qgl and objects.has_bed_mesh
        assert objects.chamber_heaters == ()

    def test_prefers_main_led_over_other_lights(self):
        objects = DiscoveredObjects.from_object_list(
            ["neopixel strip", "output_pin caselight", "output_pin main_led"]
        )
        assert objects.light == "output_pin main_led"

    def test_falls_back_to_led_strip(self):
        objects = DiscoveredObjects.from_object_list(["output_pin beeper", "neopixel strip"])
        assert objects.light == "neopixel strip"

    def test_no_light_and_no_leveling_on_minimal_printer(self):
        objects = DiscoveredObjects.from_object_list(["extruder", "heater_bed", 42])
        assert objects.light is None
        assert not objects.has_qgl and not objects.has_bed_mesh

    def test_all_objects_includes_discovered(self):
        everything = sv08_objects().all_objects()
        assert "print_stats" in everything
        assert "quad_gantry_level" in everything
        assert "output_pin main_led" in everything
        assert "fan_generic exhaust_fan" in everything

    def test_object_label(self):
        assert object_label("temperature_sensor host_temp") == "Host Temp"


class TestApplyStatus:
    def test_full_status(self):
        data = SovolPrinterData()

        assert apply_status(data, copy.deepcopy(SV08_STATUS), sv08_objects())

        assert data.klipper_state == "ready"
        assert data.print_stats.state == "printing"
        assert data.print_stats.current_layer == 12
        assert data.print_stats.total_layer == 0
        assert data.extruder.temperature == 220.0
        assert data.heater_bed.target == 65.0
        assert data.fan_speed == 80.0
        assert data.speed_factor_pct == 150
        assert data.flow_rate_pct == 95
        assert data.qgl_applied is True
        assert data.bed_mesh_profile == "default"
        assert data.extra_fans["heater_fan hotend_fan"] == 100.0
        assert data.temperature_sensors["temperature_sensor mcu_temp"] == 41.2
        assert data.filament_sensors["filament_switch_sensor filament_sensor"].filament_detected
        assert data.light_on is True
        assert data.is_homed
        assert data.idle_state == "Printing"
        assert data.display_message == "Printing benchy"

    def test_partial_update_keeps_other_fields(self):
        data = SovolPrinterData()
        objects = sv08_objects()
        apply_status(data, copy.deepcopy(SV08_STATUS), objects)

        apply_status(data, {"extruder": {"temperature": 150.04}}, objects)

        assert data.extruder.temperature == 150.0
        assert data.extruder.target == 220.0
        assert data.print_stats.filename == "benchy.gcode"

    def test_unknown_objects_report_no_change(self):
        assert not apply_status(SovolPrinterData(), {"motion_report": {}}, sv08_objects())

    def test_light_off_and_led_color_data(self):
        data = SovolPrinterData()
        apply_status(data, {"output_pin main_led": {"value": 0}}, sv08_objects())
        assert data.light_on is False

        led = DiscoveredObjects.from_object_list(["neopixel strip"])
        apply_status(data, {"neopixel strip": {"color_data": [[0, 0, 0.5, 0]]}}, led)
        assert data.light_on is True

    def test_empty_mesh_profile_is_none(self):
        data = SovolPrinterData()
        apply_status(data, {"bed_mesh": {"profile_name": ""}}, sv08_objects())
        assert data.bed_mesh_profile is None

    def test_chamber_heater(self):
        objects = DiscoveredObjects.from_object_list(["heater_generic chamber"])
        data = SovolPrinterData()
        apply_status(data, {"heater_generic chamber": {"temperature": 40.06, "target": 45}}, objects)
        assert data.chamber_heaters["heater_generic chamber"].temperature == 40.1

    def test_none_values_are_ignored(self):
        data = SovolPrinterData()
        apply_status(data, {"fan": {"speed": None}, "extruder": {"temperature": None}}, sv08_objects())
        assert data.fan_speed == 0.0
        assert data.extruder.temperature == 0.0


class TestConfigAndFiles:
    def test_config_limits(self):
        objects = DiscoveredObjects.from_object_list(["heater_generic Chamber"])
        data = SovolPrinterData()
        settings = {
            **SV08_SETTINGS,
            "heater_generic chamber": {"max_temp": 70, "min_temp": -5},
        }

        apply_config_limits(data, settings, objects)

        assert data.extruder.max_temp == 305
        assert data.heater_bed.max_temp == 110
        assert data.chamber_heaters["heater_generic Chamber"].max_temp == 70
        assert data.chamber_heaters["heater_generic Chamber"].min_temp == 0

    def test_bad_limits_keep_defaults(self):
        data = SovolPrinterData()
        apply_config_limits(data, {"extruder": {"max_temp": "hot"}}, DiscoveredObjects())
        assert data.extruder.max_temp == 300

    def test_metadata(self):
        meta = parse_file_metadata(
            {"estimated_time": 2400.5, "layer_count": 120, "slicer": "OrcaSlicer"}
        )
        assert meta.estimated_time == 2400.5
        assert meta.layer_count == 120
        assert meta.slicer == "OrcaSlicer"
        assert parse_file_metadata({"estimated_time": 0}).estimated_time is None

    def test_file_list_sorted_newest_first(self):
        files = [
            {"path": "a.gcode", "modified": 1},
            {"filename": "b.gcode", "modified": 3},
            {"path": ""},
            "junk",
            {"path": "c.gcode"},
        ]
        assert parse_file_list(files) == ["b.gcode", "a.gcode", "c.gcode"]
        assert parse_file_list(None) == []
