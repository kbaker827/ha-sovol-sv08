"""Tests for pysovol.parser (no network)."""
from __future__ import annotations

import copy

from fake_moonraker import SV08_EDDY_OBJECTS, SV08_OBJECTS, SV08_SETTINGS, SV08_STATUS
from pysovol.models import SovolPrinterData
from pysovol.parser import (
    DiscoveredObjects,
    apply_config_limits,
    apply_status,
    object_label,
    parse_file_list,
    parse_file_metadata,
    parse_spool,
)


def sv08_objects() -> DiscoveredObjects:
    return DiscoveredObjects.from_object_list(SV08_OBJECTS)


class TestDiscovery:
    def test_finds_sv08_objects(self):
        objects = sv08_objects()

        assert objects.filament_sensors == ("filament_switch_sensor filament_sensor",)
        assert objects.temperature_sensors == (
            "temperature_sensor mcu_temp",
            "temperature_sensor Host_temp",
            "temperature_sensor Toolhead_Temp",
        )
        assert objects.extra_fans == (
            "fan_generic fan0",
            "fan_generic fan1",
            "fan_generic fan3",
            "heater_fan hotend_fan",
        )
        assert objects.part_fans == ("fan_generic fan0", "fan_generic fan1")
        # The beeper pin and the screen's neopixel must not win over the LED bar.
        assert objects.light == "output_pin main_led"
        assert objects.has_qgl and objects.has_bed_mesh and objects.has_probe
        assert objects.has_z_offset_calibration
        assert objects.eddy_probe is None
        assert objects.chamber_heaters == ()

    def test_finds_sovol_eddy_probe(self):
        objects = DiscoveredObjects.from_object_list(SV08_EDDY_OBJECTS)
        assert objects.eddy_probe == "probe_eddy_current eddy"
        assert "probe_eddy_current eddy" in objects.all_objects()

    def test_printer_with_a_fan_section_uses_it(self):
        objects = DiscoveredObjects.from_object_list(["fan", "fan_generic fan0"])
        assert objects.part_fans == ("fan",)

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
        assert "fan_generic fan3" in everything
        assert "probe" in everything
        assert "fan" not in everything  # a stock SV08 has no [fan]

    def test_object_label(self):
        assert object_label("temperature_sensor host_temp") == "Host Temp"
        assert object_label("fan_generic fan0") == "Back Part Fan"
        assert object_label("fan_generic fan3") == "Exhaust Fan"
        assert object_label("fan_generic nevermore") == "Nevermore"


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

    def test_part_fan_speed_from_sv08_fan_generics(self):
        data = SovolPrinterData()
        objects = sv08_objects()
        apply_status(data, {"fan_generic fan0": {"speed": 0.4}, "fan_generic fan1": {"speed": 0.6}}, objects)
        assert data.fan_speed == 60.0
        # The exhaust fan is not part cooling.
        apply_status(data, {"fan_generic fan3": {"speed": 1.0}}, objects)
        assert data.fan_speed == 60.0

    def test_klipper_fan_section(self):
        data = SovolPrinterData()
        objects = DiscoveredObjects.from_object_list(["fan"])
        apply_status(data, {"fan": {"speed": 0.25}}, objects)
        assert data.fan_speed == 25.0

    def test_eddy_probe_status(self):
        data = SovolPrinterData()
        objects = DiscoveredObjects.from_object_list(SV08_EDDY_OBJECTS)
        apply_status(
            data,
            {"probe_eddy_current eddy": {"last_query": True, "last_z_result": 0.01234, "is_calibrated": False}},
            objects,
        )
        assert data.probe_last_z == 0.0123
        assert data.probe_triggered is True
        assert data.eddy_calibrated is False

    def test_inductive_probe_has_no_calibration_flag(self):
        data = SovolPrinterData()
        apply_status(data, {"probe": {"last_query": False, "last_z_result": -0.05}}, sv08_objects())
        assert data.probe_last_z == -0.05
        assert data.eddy_calibrated is None

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

    def test_metadata_filament_first_extruder(self):
        meta = parse_file_metadata(
            {
                "filament_type": "PETG;PLA",
                "filament_name": '"Sovol PETG Blue";"Generic PLA"',
                "filament_colors": ["#0099ff", "#FFFFFF"],
            }
        )
        assert meta.filament_type == "PETG"
        assert meta.filament_name == "Sovol PETG Blue"
        assert meta.filament_color == "#0099FF"

    def test_metadata_without_filament(self):
        meta = parse_file_metadata({"filament_colors": ["not-a-colour"], "filament_type": ""})
        assert meta.filament_type is None
        assert meta.filament_color is None

    def test_parse_spool(self):
        spool = parse_spool({
    "id": 7,
    "remaining_weight": 612.42,
    "filament": {
        "name": "SV PLA+ Galaxy Black",
        "material": "PLA",
        "color_hex": "1a1a2e",
        "vendor": {"name": "Sovol"},
    },
})
        assert spool.spool_id == 7
        assert spool.material == "PLA"
        assert spool.name == "SV PLA+ Galaxy Black"
        assert spool.vendor == "Sovol"
        assert spool.color == "#1A1A2E"
        assert spool.remaining_weight == 612.4

    def test_parse_spool_multicolour_and_junk(self):
        spool = parse_spool({"id": 3, "filament": {"multi_color_hexes": "ff0000,00ff00"}})
        assert spool.color == "#FF0000"
        assert spool.material is None
        assert parse_spool(None) is None
        assert parse_spool({"filament": {}}) is None

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
