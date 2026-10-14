"""Tests for pysovol.client against a fake SV08 Moonraker."""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from pysovol import MoonrakerAuthError, MoonrakerError, SovolClient


async def wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


class TestInit:
    async def test_initial_state(self, client):
        data = client.data
        assert client.connected
        assert data.printer_name == "sv08"
        assert data.firmware_version == "v0.12.0-sovol"
        assert data.extruder.max_temp == 305
        assert data.heater_bed.max_temp == 110
        assert data.print_stats.filename == "benchy.gcode"
        assert data.file_metadata.estimated_time == 2400
        assert data.total_layers == 120
        assert data.available_files == ["benchy.gcode", "cube.gcode", "old.gcode"]
        assert client.objects.light == "output_pin main_led"

    async def test_unreachable_printer(self):
        sv08 = SovolClient("127.0.0.1", 1)
        assert not await sv08.async_init()
        await sv08.async_stop()

    async def test_default_camera_urls(self, client):
        assert client.camera_stream_url.endswith("/webcam/?action=stream")
        assert client.camera_snapshot_url.endswith("/webcam/?action=snapshot")

    async def test_webcam_discovery_skips_screen_mirror(self, moonraker):
        moonraker.webcams = [
            {"name": "gui", "stream_url": "/screen/stream"},
            {"name": "camera", "stream_url": "/webcam/?action=stream",
             "snapshot_url": "http://localhost:8080/?action=snapshot"},
        ]
        sv08 = SovolClient("127.0.0.1", moonraker.port)
        await sv08.async_init()
        assert sv08.camera_stream_url == f"http://127.0.0.1:{moonraker.port}/webcam/?action=stream"
        assert sv08.camera_snapshot_url == "http://127.0.0.1:8080/?action=snapshot"
        assert sv08.webcam_info["name"] == "camera"
        await sv08.async_stop()

    async def test_injected_session_is_not_closed(self, moonraker):
        async with aiohttp.ClientSession() as session:
            sv08 = SovolClient("127.0.0.1", moonraker.port, session=session)
            assert await sv08.async_init()
            await sv08.async_stop()
            assert not session.closed


class TestUrls:
    @pytest.mark.parametrize(
        ("port", "base", "ws"),
        [
            (80, "http://sv08", "ws://sv08/websocket"),
            (443, "https://sv08", "wss://sv08/websocket"),
            (7125, "http://sv08:7125", "ws://sv08:7125/websocket"),
        ],
    )
    def test_base_and_ws_url(self, port, base, ws):
        sv08 = SovolClient("sv08", port)
        assert sv08.base_url == base
        assert sv08.ws_url == ws

    def test_api_key_header(self):
        assert SovolClient("sv08", api_key="abc").headers == {"X-Api-Key": "abc"}
        assert SovolClient("sv08").headers == {}


class TestCommands:
    async def test_print_controls(self, client, moonraker):
        await client.pause_print()
        await client.resume_print()
        await client.cancel_print()
        await client.emergency_stop()
        await client.restart_klipper()
        await client.firmware_restart()
        await client.start_print("cube.gcode")
        assert moonraker.posts == [
            "/printer/print/pause",
            "/printer/print/resume",
            "/printer/print/cancel",
            "/printer/emergency_stop",
            "/printer/restart",
            "/printer/firmware_restart",
            "/printer/print/start",
        ]

    async def test_gcode_commands(self, client, moonraker):
        await client.home_axes()
        await client.home_axes("Z")
        await client.set_bed_temperature(60)
        await client.set_nozzle_temperature(215.5)
        await client.set_fan_speed(50)
        await client.set_extra_fan_speed("fan_generic exhaust_fan", 75)
        await client.set_speed_factor(120)
        await client.set_flow_rate(98)
        assert moonraker.gcode == [
            "G28",
            "G28 Z",
            "M140 S60",
            "M104 S215.5",
            "M106 S128",
            "SET_FAN_SPEED FAN=exhaust_fan SPEED=0.75",
            "M220 S120",
            "M221 S98",
        ]

    async def test_leveling_homes_only_when_needed(self, client, moonraker):
        await client.quad_gantry_level()
        client.data.toolhead.homed_axes = ""
        await client.bed_mesh_calibrate()
        client.data.qgl_applied = False
        await client.bed_mesh_calibrate()
        assert moonraker.gcode == [
            "QUAD_GANTRY_LEVEL\nG28 Z",
            "G28\nBED_MESH_CALIBRATE",
            "G28\nQUAD_GANTRY_LEVEL\nG28 Z\nBED_MESH_CALIBRATE",
        ]

    async def test_light_output_pin(self, client, moonraker):
        await client.set_light(False)
        assert moonraker.gcode == ["SET_PIN PIN=main_led VALUE=0"]
        assert client.data.light_on is False

    async def test_light_led_strip(self, moonraker):
        moonraker.objects = ["extruder", "heater_bed", "neopixel strip"]
        sv08 = SovolClient("127.0.0.1", moonraker.port)
        await sv08.async_init()
        await sv08.set_light(True)
        assert moonraker.gcode == ["SET_LED LED=strip RED=1 GREEN=1 BLUE=1 WHITE=1"]
        await sv08.async_stop()

    async def test_no_light(self, moonraker):
        moonraker.objects = ["extruder", "heater_bed"]
        sv08 = SovolClient("127.0.0.1", moonraker.port)
        await sv08.async_init()
        with pytest.raises(ValueError):
            await sv08.set_light(True)
        await sv08.async_stop()

    @pytest.mark.parametrize(
        "call",
        [
            lambda c: c.set_bed_temperature(111),
            lambda c: c.set_nozzle_temperature(-1),
            lambda c: c.set_fan_speed(101),
            lambda c: c.set_speed_factor(5),
            lambda c: c.set_flow_rate(200),
            lambda c: c.set_extra_fan_speed("heater_fan hotend_fan", 50),
            lambda c: c.set_chamber_heater_temperature("heater_generic nope", 40),
        ],
    )
    async def test_rejects_invalid_values(self, client, moonraker, call):
        with pytest.raises(ValueError):
            await call(client)
        assert moonraker.gcode == []

    async def test_chamber_heater(self, moonraker):
        moonraker.objects = [*moonraker.objects, "heater_generic chamber"]
        moonraker.settings["heater_generic chamber"] = {"min_temp": 10, "max_temp": 60}
        sv08 = SovolClient("127.0.0.1", moonraker.port)
        await sv08.async_init()
        await sv08.set_chamber_heater_temperature("heater_generic chamber", 0)
        await sv08.set_chamber_heater_temperature("heater_generic chamber", 45)
        with pytest.raises(ValueError):
            await sv08.set_chamber_heater_temperature("heater_generic chamber", 5)
        assert moonraker.gcode == [
            "SET_HEATER_TEMPERATURE HEATER=chamber TARGET=0",
            "SET_HEATER_TEMPERATURE HEATER=chamber TARGET=45",
        ]
        await sv08.async_stop()

    async def test_gcode_error_message(self, client, moonraker):
        moonraker.gcode_error = "Must home axis first"
        with pytest.raises(MoonrakerError, match="Must home axis first"):
            await client.execute_gcode("G1 X10")


class TestConnection:
    async def test_test_connection(self, moonraker):
        async with aiohttp.ClientSession() as session:
            ok = await SovolClient.test_connection(session, "127.0.0.1", moonraker.port)
            down = await SovolClient.test_connection(session, "127.0.0.1", 1)
        assert ok is None
        assert down == "cannot_connect"

    async def test_test_connection_with_api_key(self, moonraker):
        moonraker.api_key = "secret"
        async with aiohttp.ClientSession() as session:
            missing = await SovolClient.test_connection(session, "127.0.0.1", moonraker.port)
            good = await SovolClient.test_connection(
                session, "127.0.0.1", moonraker.port, "secret"
            )
        assert missing == "invalid_auth"
        assert good is None

    async def test_rejected_api_key_raises(self, moonraker):
        moonraker.api_key = "secret"
        sv08 = SovolClient("127.0.0.1", moonraker.port, api_key="wrong")
        with pytest.raises(MoonrakerAuthError):
            await sv08.async_init()
        await sv08.async_stop()

    async def test_non_json_reply_is_a_moonraker_error(self, client):
        with pytest.raises(MoonrakerError, match="Unexpected reply"):
            await client._get("/starting")


class TestWebSocket:
    async def test_subscribes_and_receives_updates(self, client, moonraker):
        moonraker.ws_messages = [
            {"method": "notify_status_update",
             "params": [{"extruder": {"temperature": 180.0}}, 1234.5]},
        ]
        updates = []
        client.register_callback(updates.append)

        await client.async_start()
        await wait_for(lambda: client.data.extruder.temperature == 180.0)

        subscribed = moonraker.subscriptions[0]["params"]["objects"]
        assert "quad_gantry_level" in subscribed and "output_pin main_led" in subscribed
        assert updates

    async def test_async_callback_and_failing_callback(self, client):
        seen = []

        async def good(data):
            seen.append(data)

        def bad(data):
            raise RuntimeError("boom")

        client.register_callback(bad)
        client.register_callback(good)
        await client.handle_ws_message(
            {"method": "notify_status_update", "params": [{"fan": {"speed": 0.1}}]}
        )
        assert seen and client.data.fan_speed == 10.0
        client.remove_callback(good)
        client.remove_callback(good)

    async def test_klippy_shutdown_and_ready(self, client):
        await client.handle_ws_message({"method": "notify_klippy_shutdown"})
        assert client.data.klipper_state == "shutdown"
        await client.handle_ws_message({"method": "notify_klippy_ready"})
        assert client.data.klipper_state == "ready"

    async def test_filelist_and_history_changed(self, client, moonraker):
        moonraker.files = [{"path": "new.gcode", "modified": 999}]
        await client.handle_ws_message({"method": "notify_filelist_changed"})
        assert client.data.available_files == ["new.gcode"]

        moonraker.status["print_stats"]["state"] = "complete"
        await client.handle_ws_message({"method": "notify_history_changed"})
        assert client.data.print_stats.state == "complete"

    async def test_metadata_cleared_when_file_changes(self, client):
        await client.handle_ws_message(
            {"method": "notify_status_update", "params": [{"print_stats": {"filename": "x.gcode"}}]}
        )
        assert client.data.file_metadata.estimated_time is None
        await client.handle_ws_message(
            {"method": "notify_status_update", "params": [{"print_stats": {"filename": ""}}]}
        )
        assert client.data.total_layers == 0

    async def test_metadata_retried_after_failure(self, client, moonraker):
        await client.handle_ws_message(
            {"method": "notify_status_update", "params": [{"print_stats": {"filename": "new.gcode"}}]}
        )
        assert client.data.file_metadata.estimated_time is None
        moonraker.metadata["new.gcode"] = {"estimated_time": 600}
        await client.handle_ws_message(
            {"method": "notify_status_update", "params": [{"print_stats": {"print_duration": 1}}]}
        )
        assert client.data.file_metadata.estimated_time == 600

    async def test_reconnects_after_server_drop(self, moonraker):
        sv08 = SovolClient("127.0.0.1", moonraker.port)
        await sv08.async_init()
        await sv08.async_start()
        await wait_for(lambda: len(moonraker.subscriptions) == 1)
        sv08._reconnect_delay = 1  # keep the test quick
        await sv08._ws.close()
        await wait_for(lambda: len(moonraker.subscriptions) == 2, timeout=8)
        # A connection that came up resets the back-off.
        assert sv08._reconnect_delay == 5
        await sv08.async_stop()
