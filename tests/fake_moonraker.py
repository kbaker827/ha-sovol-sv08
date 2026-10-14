"""A fake Moonraker that looks like a stock Sovol SV08."""
from __future__ import annotations

import copy
import json

from aiohttp import web

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
    "gcode_macro PRINT_START",
    "heaters",
    "idle_timeout",
    "toolhead",
    "extruder",
    "heater_bed",
    "fan",
    "heater_fan hotend_fan",
    "controller_fan controller_fan",
    "fan_generic exhaust_fan",
    "temperature_sensor mcu_temp",
    "temperature_sensor host_temp",
    "filament_switch_sensor filament_sensor",
    "output_pin main_led",
    "probe",
    "quad_gantry_level",
    "bed_mesh",
    "motion_report",
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
    "fan": {"speed": 0.8},
    "display_status": {"message": "Printing benchy"},
    "idle_timeout": {"state": "Printing"},
    "quad_gantry_level": {"applied": True},
    "bed_mesh": {"profile_name": "default"},
    "heater_fan hotend_fan": {"speed": 1.0},
    "controller_fan controller_fan": {"speed": 0.5},
    "fan_generic exhaust_fan": {"speed": 0.0},
    "temperature_sensor mcu_temp": {"temperature": 41.23},
    "temperature_sensor host_temp": {"temperature": 52.1},
    "filament_switch_sensor filament_sensor": {"enabled": True, "filament_detected": True},
    "output_pin main_led": {"value": 1.0},
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
        self.metadata = {"benchy.gcode": {"estimated_time": 2400, "layer_count": 120}}
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
