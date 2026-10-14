"""End-to-end tests: the integration running in Home Assistant."""
from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.sovol_sv08.const import DOMAIN
from custom_components.sovol_sv08.diagnostics import async_get_config_entry_diagnostics


class TestConfigFlow:
    async def test_user_flow_creates_entry(self, hass, moonraker):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": " http://127.0.0.1/ ", "port": moonraker.port, "api_key": ""},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Sovol SV08"
        assert result["data"] == {"host": "127.0.0.1", "port": moonraker.port, "api_key": None}
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(result["result"].entry_id)
        await hass.async_block_till_done()

    async def test_user_flow_cannot_connect(self, hass, socket_enabled):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "127.0.0.1", "port": 1, "api_key": ""}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_duplicate_aborts(self, hass, setup_entry, moonraker):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "127.0.0.1", "port": moonraker.port, "api_key": ""}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"

    async def test_options_flow(self, hass, setup_entry):
        result = await hass.config_entries.options.async_init(setup_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 60, "temperature_unit": "fahrenheit"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        state = hass.states.get("sensor.sovol_sv08_nozzle_temperature")
        assert state.attributes["unit_of_measurement"] == "°F"


class TestSetup:
    async def test_unreachable_printer_retries(self, hass, entry, moonraker):
        entry.add_to_hass(hass)
        hass.config_entries.async_update_entry(entry, data={**entry.data, "port": 1})
        await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY

    async def test_entities(self, hass, setup_entry):
        states = {
            "sensor.sovol_sv08_print_state": "printing",
            "sensor.sovol_sv08_print_progress": "50.0",
            "sensor.sovol_sv08_nozzle_temperature": "220.0",
            "sensor.sovol_sv08_total_layers": "120",
            "sensor.sovol_sv08_bed_mesh_profile": "default",
            "sensor.sovol_sv08_mcu_temp": "41.2",
            "sensor.sovol_sv08_exhaust_fan": "0.0",
            "sensor.sovol_sv08_toolhead_temp": "36.4",
            "binary_sensor.sovol_sv08_gantry_leveled": "on",
            "binary_sensor.sovol_sv08_filament_sensor": "off",
            "switch.sovol_sv08_light": "on",
            "select.sovol_sv08_print_file": "benchy.gcode",
        }
        for entity_id, expected in states.items():
            state = hass.states.get(entity_id)
            assert state is not None, entity_id
            assert state.state == expected, entity_id
        assert hass.states.get("number.sovol_sv08_nozzle_target").attributes["max"] == 305
        assert hass.states.get("button.sovol_sv08_quad_gantry_level") is not None
        assert hass.states.get("number.sovol_sv08_exhaust_fan_speed") is not None
        # Leveling is not allowed mid-print
        assert hass.states.get("button.sovol_sv08_quad_gantry_level").state == "unavailable"

    async def test_stock_sv08_fans_and_probe(self, hass, setup_entry):
        # No [fan] on an SV08: part cooling is read from fan0/fan1 (80 % in the fake).
        assert hass.states.get("sensor.sovol_sv08_part_cooling_fan").state == "80.0"
        assert hass.states.get("sensor.sovol_sv08_back_part_fan").state == "80.0"
        assert hass.states.get("number.sovol_sv08_exhaust_fan_speed") is not None
        assert hass.states.get("sensor.sovol_sv08_probe_z_result").state == "0.0123"
        assert hass.states.get("button.sovol_sv08_z_offset_calibrate") is not None
        # Inductive probe: no eddy entity
        assert hass.states.get("binary_sensor.sovol_sv08_eddy_probe_calibrated") is None

    async def test_sovol_eddy_upgrade(self, hass, entry, moonraker):
        from fake_moonraker import SV08_EDDY_OBJECTS

        moonraker.objects = list(SV08_EDDY_OBJECTS)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get("binary_sensor.sovol_sv08_eddy_probe_calibrated").state == "on"
        assert hass.states.get("sensor.sovol_sv08_probe_z_result").state == "0.0123"
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    async def test_unique_ids_use_entry_id(self, hass, setup_entry):
        registry = er.async_get(hass)
        reg = registry.async_get("sensor.sovol_sv08_print_state")
        assert reg.unique_id == f"{setup_entry.entry_id}_print_state"

    async def test_controls_send_gcode(self, hass, setup_entry, moonraker):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": "switch.sovol_sv08_light"}, blocking=True
        )
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": "number.sovol_sv08_bed_target", "value": 70}, blocking=True,
        )
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.sovol_sv08_pause_print"}, blocking=True
        )
        assert moonraker.gcode == ["SET_PIN PIN=main_led VALUE=0", "M140 S70"]
        assert moonraker.posts == ["/printer/print/pause"]
        assert hass.states.get("switch.sovol_sv08_light").state == "off"

    async def test_number_out_of_range(self, hass, setup_entry):
        with pytest.raises(Exception):
            await hass.services.async_call(
                "number", "set_value",
                {"entity_id": "number.sovol_sv08_bed_target", "value": 500}, blocking=True,
            )

    async def test_select_refuses_second_print(self, hass, setup_entry):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "select", "select_option",
                {"entity_id": "select.sovol_sv08_print_file", "option": "cube.gcode"},
                blocking=True,
            )

    async def test_services(self, hass, setup_entry, moonraker):
        await hass.services.async_call(
            DOMAIN, "execute_gcode",
            {"config_entry_id": setup_entry.entry_id, "script": "G28"}, blocking=True,
        )
        await hass.services.async_call(
            DOMAIN, "set_nozzle_temperature",
            {"config_entry_id": setup_entry.entry_id, "temperature": 200}, blocking=True,
        )
        assert moonraker.gcode == ["G28", "M104 S200"]
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "set_bed_temperature",
                {"config_entry_id": setup_entry.entry_id, "temperature": 150}, blocking=True,
            )
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN, "execute_gcode",
                {"config_entry_id": "nope", "script": "G28"}, blocking=True,
            )

    async def test_print_complete_fires_event(self, hass, setup_entry):
        events = []
        hass.bus.async_listen("sovol_sv08_print_complete", events.append)
        client = hass.data[DOMAIN][setup_entry.entry_id].client
        await client.handle_ws_message(
            {"method": "notify_status_update",
             "params": [{"print_stats": {"state": "complete"}}]}
        )
        await hass.async_block_till_done()
        assert len(events) == 1
        assert events[0].data["filename"] == "benchy.gcode"
        assert "device_id" in events[0].data

    async def test_resume_is_not_a_new_print(self, hass, setup_entry):
        started = []
        hass.bus.async_listen("sovol_sv08_print_started", started.append)
        client = hass.data[DOMAIN][setup_entry.entry_id].client
        for state in ("paused", "printing"):
            await client.handle_ws_message(
                {"method": "notify_status_update", "params": [{"print_stats": {"state": state}}]}
            )
        await hass.async_block_till_done()
        assert started == []

    async def test_rejected_api_key_starts_reauth(self, hass, entry, moonraker):
        moonraker.api_key = "secret"
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_ERROR
        flows = hass.config_entries.flow.async_progress()
        assert [f["context"]["source"] for f in flows] == ["reauth"]

        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"], {"api_key": "secret"}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data["api_key"] == "secret"

    async def test_unload_removes_services(self, hass, setup_entry):
        assert await hass.config_entries.async_unload(setup_entry.entry_id)
        assert not hass.services.has_service(DOMAIN, "execute_gcode")


class TestFrontendAndDiagnostics:
    async def test_card_entities_command(self, hass, setup_entry, hass_ws_client):
        ws = await hass_ws_client(hass)
        await ws.send_json({"id": 1, "type": "sovol_sv08/entities"})
        msg = await ws.receive_json()
        printer = msg["result"]["printers"][0]
        assert printer["entry_id"] == setup_entry.entry_id
        roles = printer["entities"]
        assert roles["sensor.print_progress"] == "sensor.sovol_sv08_print_progress"
        assert roles["number.nozzle_target_setpoint"] == "number.sovol_sv08_nozzle_target"
        assert roles["switch.light"] == "switch.sovol_sv08_light"
        assert roles["camera.webcam"] == "camera.sovol_sv08_webcam"

    async def test_diagnostics_redacts_api_key(self, hass, setup_entry):
        hass.config_entries.async_update_entry(
            setup_entry, data={**setup_entry.data, "api_key": "secret"}
        )
        diag = await async_get_config_entry_diagnostics(hass, setup_entry)
        assert diag["config_entry"]["api_key"] == "**REDACTED**"
        assert diag["discovered_objects"]["light"] == "output_pin main_led"
