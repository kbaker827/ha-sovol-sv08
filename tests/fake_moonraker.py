"""A fake Moonraker that looks like a stock Sovol SV08."""
from __future__ import annotations

import copy
import json

from aiohttp import web

# Object names from Sovol's stock SV08 printer.cfg (github.com/Sovol3d/SV08).
# Note there is no [fan]: part cooling is fan_generic fan0 (back) + fan1 (front).
SV08_OBJECTS = [
    "webhooks",
    "configfile",
    "mcu",
    "mcu extra_mcu",
    "gcode_move",
    "print_stats",
    "virtual_sdcard",
    "pause_resume",
    "display_status",
    "gcode_macro QUAD_GANTRY_LEVEL",
    "gcode_macro BED_MESH_CALIBRATE",
    "heaters",
    "idle_timeout",
    "toolhead",
    "extruder",
    "heater_bed",
    "fan_generic fan0",
    "fan_generic fan1",
    "fan_generic fan3",
    "heater_fan hotend_fan",
    "temperature_sensor mcu_temp",
    "temperature_sensor Host_temp",
    "temperature_sensor Toolhead_Temp",
    "filament_switch_sensor filament_sensor",
    "output_pin beeper",
    "neopixel Screen_Colour",
    "output_pin main_led",
    "probe",
    "z_offset_calibration",
    "quad_gantry_level",
    "bed_mesh",
    "motion_report",
]

# Sovol's eddy upgrade, as on the SV08 Max: [probe_eddy_current eddy]
# replaces [probe] (and registers itself as "probe" too).
SV08_EDDY_OBJECTS = [
    *[o for o in SV08_OBJECTS if o != "probe"],
    "probe",
    "probe_eddy_current eddy",
]

SV08_STATUS = {
    "webhooks": {"state": "ready", "state_message": "Printer is ready"},
    "print_stats": {
        "state": "printing",
        "filename": "benchy.gcode",
        "total_duration": 700.0,
        "print_duration": 600.0,
        "filament_used": 1234.56,
        "info": {"current_layer": 12, "total_layer": None},
    },
    "virtual_sdcard": {"progress": 0.5, "is_active": True, "file_position": 1000},
    "extruder": {"temperature": 219.96, "target": 220.0, "power": 0.41},
    "heater_bed": {"temperature": 64.98, "target": 65.0, "power": 0.2},
    "toolhead": {"position": [175.0, 175.0, 2.4, 100.0], "homed_axes": "xyz"},
    "gcode_move": {"speed_factor": 1.5, "extrude_factor": 0.95},
    "display_status": {"message": "Printing benchy"},
    "idle_timeout": {"state": "Printing"},
    "quad_gantry_level": {"applied": True},
    "bed_mesh": {"profile_name": "default"},
    "fan_generic fan0": {"speed": 0.8},
    "fan_generic fan1": {"speed": 0.8},
    "fan_generic fan3": {"speed": 0.0},
    "heater_fan hotend_fan": {"speed": 1.0},
    "temperature_sensor mcu_temp": {"temperature": 41.23},
    "temperature_sensor Host_temp": {"temperature": 52.1},
    "temperature_sensor Toolhead_Temp": {"temperature": 36.4},
    "filament_switch_sensor filament_sensor": {"enabled": True, "filament_detected": True},
    "output_pin main_led": {"value": 1.0},
    "probe": {"name": "probe", "last_query": False, "last_z_result": 0.0123},
    "probe_eddy_current eddy": {
        "name": "probe_eddy_current eddy",
        "last_query": False,
        "last_z_result": 0.0123,
        "is_calibrated": True,
    },
}

SV08_SETTINGS = {
    "extruder": {"max_temp": 305, "min_temp": 0},
    "heater_bed": {"max_temp": 110, "min_temp": 0},
}


class FakeMoonraker:
    """Minimal Moonraker HTTP + WebSocket API."""

    def __init__(self) -> None:
        self.objects = list(SV08_OBJECTS)
        self.status = copy.deepcopy(SV08_STATUS)
        self.settings = copy.deepcopy(SV08_SETTINGS)
        self.gcode: list[str] = []
        self.posts: list[str] = []
        self.gcode_error: str | None = None
        self.files = [
            {"path": "old.gcode", "modified": 100.0},
            {"path": "benchy.gcode", "modified": 300.0},
            {"path": "cube.gcode", "modified": 200.0},
        ]
        self.metadata = {
            "benchy.gcode": {
                "estimated_time": 2400,
                "layer_count": 120,
                "slicer": "OrcaSlicer",
                "filament_type": "PETG;PLA",
                "filament_name": '"Sovol PETG Blue";"Generic PLA"',
                "filament_colors": ["#0099ff", "#FFFFFF"],
            }
        }
        # None = Moonraker without a [spoolman] section (endpoints answer 404)
        self.spoolman: dict | None = None
        self.webcams: list[dict] = []
        self.api_key: str | None = None
        self.ws_messages: list[dict] = []
        self.subscriptions: list[dict] = []
        self.app = web.Application()
        self.app.router.add_get("/printer/info", self._info)
        self.app.router.add_get("/server/info", self._server_info)
        self.app.router.add_get("/printer/objects/list", self._objects)
        self.app.router.add_get("/printer/objects/query", self._query)
        self.app.router.add_get("/server/files/list", self._files)
        self.app.router.add_get("/server/files/metadata", self._metadata)
        self.app.router.add_get("/server/webcams/list", self._webcams)
        self.app.router.add_post("/printer/gcode/script", self._gcode)
        self.app.router.add_get("/server/spoolman/status", self._spoolman_status)
        self.app.router.add_post("/server/spoolman/proxy", self._spoolman_proxy)
        self.app.router.add_post("/printer/{tail:.*}", self._post)
        self.app.router.add_get("/websocket", self._websocket)
        # nginx's placeholder page while Moonraker is still starting
        self.app.router.add_get("/starting", self._html)

    def _authorised(self, request: web.Request) -> bool:
        return self.api_key is None or request.headers.get("X-Api-Key") == self.api_key

    @staticmethod
    def _result(result) -> web.Response:
        return web.json_response({"result": result})

    async def _info(self, request):
        if not self._authorised(request):
            return web.json_response({"error": {"message": "Unauthorized"}}, status=401)
        return self._result({"hostname": "sv08", "software_version": "v0.12.0-sovol"})

    async def _server_info(self, request):
        if not self._authorised(request):
            return web.json_response({"error": {"message": "Unauthorized"}}, status=401)
        return self._result({"klippy_state": "ready"})

    async def _objects(self, request):
        return self._result({"objects": self.objects})

    async def _query(self, request):
        if "configfile" in request.query:
            return self._result({"status": {"configfile": {"settings": self.settings}}})
        status = {k: v for k, v in self.status.items() if k in request.query}
        return self._result({"status": status})

    async def _files(self, request):
        return self._result(self.files)

    async def _metadata(self, request):
        meta = self.metadata.get(request.query.get("filename", ""))
        if meta is None:
            return web.json_response({"error": {"message": "Not found"}}, status=404)
        return self._result(meta)

    async def _webcams(self, request):
        return self._result({"webcams": self.webcams})

    async def _gcode(self, request):
        body = await request.json()
        self.gcode.append(body["script"])
        if self.gcode_error:
            return web.json_response({"error": {"message": self.gcode_error}}, status=400)
        return self._result("ok")

    async def _post(self, request):
        self.posts.append(request.path)
        return self._result("ok")

    async def _spoolman_status(self, request):
        if self.spoolman is None:
            return web.json_response({"error": {"message": "Not Found"}}, status=404)
        return self._result(
            {"spoolman_connected": True, "pending_reports": [], "spool_id": self.spoolman.get("spool_id")}
        )

    async def _spoolman_proxy(self, request):
        if self.spoolman is None:
            return web.json_response({"error": {"message": "Not Found"}}, status=404)
        body = await request.json()
        spool = self.spoolman.get("spools", {}).get(body["path"].rsplit("/", 1)[-1])
        if spool is None:
            return self._result({"response": None, "error": {"status_code": 404, "message": "No spool"}})
        return self._result({"response": spool, "error": None})

    async def _html(self, request):
        return web.Response(text="<html>starting</html>", content_type="text/html")

    async def _websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            payload = json.loads(msg.data)
            self.subscriptions.append(payload)
            await ws.send_str(
                json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": {"status": {}}})
            )
            for message in self.ws_messages:
                await ws.send_str(json.dumps(message))
        return ws
