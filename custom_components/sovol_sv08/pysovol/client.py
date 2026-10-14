"""Async Moonraker API client for the Sovol SV08."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp

from .const import (
    CAMERA_SNAPSHOT_PATH,
    CAMERA_STREAM_PATH,
    ENDPOINT_EMERGENCY_STOP,
    ENDPOINT_FILES_LIST,
    ENDPOINT_FILES_METADATA,
    ENDPOINT_FIRMWARE_RESTART,
    ENDPOINT_GCODE_SCRIPT,
    ENDPOINT_PRINT_CANCEL,
    ENDPOINT_PRINT_PAUSE,
    ENDPOINT_PRINT_RESUME,
    ENDPOINT_PRINT_START,
    ENDPOINT_PRINTER_INFO,
    ENDPOINT_PRINTER_OBJECTS_LIST,
    ENDPOINT_PRINTER_OBJECTS_QUERY,
    ENDPOINT_PRINTER_RESTART,
    ENDPOINT_SERVER_INFO,
    ENDPOINT_WEBCAMS_LIST,
    KLIPPER_READY,
    KLIPPER_SHUTDOWN,
    WS_ENDPOINT,
    WS_METHOD_SUBSCRIBE,
    WS_NOTIFY_FILELIST_CHANGED,
    WS_NOTIFY_HISTORY_CHANGED,
    WS_NOTIFY_KLIPPY_DISCONNECTED,
    WS_NOTIFY_KLIPPY_READY,
    WS_NOTIFY_KLIPPY_SHUTDOWN,
    WS_NOTIFY_STATUS_UPDATE,
)
from .models import FileMetadata, SovolPrinterData
from .parser import (
    DiscoveredObjects,
    apply_config_limits,
    apply_status,
    object_name,
    parse_file_list,
    parse_file_metadata,
)

_LOGGER = logging.getLogger(__name__)

HTTP_TIMEOUT = 10
# Moonraker answers /printer/gcode/script only once the G-code has finished,
# and QUAD_GANTRY_LEVEL / BED_MESH_CALIBRATE take minutes on a 350 mm bed.
LONG_GCODE_TIMEOUT = 900
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 60
# Moonraker pushes notify_proc_stat_update to every client about once a
# second, so this long without any message means the printer is gone. Used
# instead of aiohttp's ping heartbeat, whose timer can outlive the socket.
WS_IDLE_TIMEOUT = 30

# Webcam entries whose name/location contains one of these are screen
# mirrors rather than the printer's camera.
_SCREEN_MIRROR_WEBCAM_TOKENS = ("gui", "screen")
# Hostnames that only resolve on the printer itself.
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


class MoonrakerError(Exception):
    """Moonraker rejected a request (e.g. a G-code error)."""


class MoonrakerAuthError(MoonrakerError):
    """Moonraker refused the API key (HTTP 401/403)."""


# Everything a request to the printer can fail with
REQUEST_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError, MoonrakerError, ValueError)


def build_base_url(host: str, port: int) -> str:
    if port == 80:
        return f"http://{host}"
    if port == 443:
        return f"https://{host}"
    return f"http://{host}:{port}"


class SovolClient:
    """Async client for the Sovol SV08 Moonraker API.

    Connects via WebSocket for push updates and falls back to HTTP polling
    when the WebSocket is unavailable.
    """

    def __init__(
        self,
        host: str,
        port: int = 80,
        api_key: str | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key

        self._session = session
        self._own_session = session is None

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ws_task: asyncio.Task | None = None
        self._request_id = 0

        self._data = SovolPrinterData()
        self._objects = DiscoveredObjects()
        self._metadata_for: str | None = None
        self._connected = False
        self._callbacks: list[Callable] = []

        self._camera_stream_url: str | None = None
        self._camera_snapshot_url: str | None = None
        self._webcam_info: dict[str, Any] | None = None

        self._should_reconnect = True
        self._reconnect_delay = RECONNECT_MIN_DELAY

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return build_base_url(self.host, self.port)

    @property
    def ws_url(self) -> str:
        return self.base_url.replace("http", "ws", 1) + WS_ENDPOINT

    @property
    def camera_stream_url(self) -> str:
        return self._camera_stream_url or f"{self.base_url}{CAMERA_STREAM_PATH}"

    @property
    def camera_snapshot_url(self) -> str:
        return self._camera_snapshot_url or f"{self.base_url}{CAMERA_SNAPSHOT_PATH}"

    @property
    def webcam_info(self) -> dict[str, Any] | None:
        """Raw Moonraker entry of the discovered webcam, if any."""
        return self._webcam_info

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Api-Key": self.api_key} if self.api_key else {}

    @property
    def data(self) -> SovolPrinterData:
        return self._data

    @property
    def objects(self) -> DiscoveredObjects:
        """Klipper objects discovered on this printer."""
        return self._objects

    @property
    def connected(self) -> bool:
        return self._connected

    def resolve_url(self, url: str) -> str:
        """Turn a Moonraker-provided URL into one reachable from Home Assistant.

        Handles relative paths, protocol-relative URLs and absolute URLs that
        point at a loopback address, which only resolve on the printer.
        """
        resolved = urljoin(f"{self.base_url}/", url)
        parts = urlsplit(resolved)
        if parts.hostname in _LOOPBACK_HOSTS:
            netloc = self.host if parts.port is None else f"{self.host}:{parts.port}"
            resolved = urlunsplit(parts._replace(netloc=netloc))
        return resolved

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def register_callback(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    async def async_init(self) -> bool:
        """Open the session, discover the printer and fetch initial state."""
        if self._own_session and self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            await self._fetch_printer_info()
            await self._discover_objects()
            await self._fetch_config_limits()
            await self._discover_webcams()
            await self.fetch_state()
            await self.fetch_file_list()
        except MoonrakerAuthError:
            raise
        except REQUEST_ERRORS as exc:
            _LOGGER.error("Failed to initialise Sovol SV08 client: %s", exc)
            return False
        self._connected = True
        return True

    async def async_start(self) -> None:
        """Start the WebSocket connection loop."""
        self._should_reconnect = True
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def async_stop(self) -> None:
        """Stop all connections and clean up."""
        self._should_reconnect = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._connected = False
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    async def _fetch_printer_info(self) -> None:
        data = await self._get(ENDPOINT_PRINTER_INFO)
        result = data.get("result", {})
        self._data.firmware_version = result.get("software_version", "")
        if result.get("hostname"):
            self._data.printer_name = result["hostname"]

    async def _discover_objects(self) -> None:
        data = await self._get(ENDPOINT_PRINTER_OBJECTS_LIST)
        objects = data.get("result", {}).get("objects", [])
        self._objects = DiscoveredObjects.from_object_list(objects)
        _LOGGER.debug("Discovered Klipper objects: %s", self._objects)

    async def _fetch_config_limits(self) -> None:
        """Read heater limits from the Klipper config (best effort)."""
        try:
            data = await self._get(
                ENDPOINT_PRINTER_OBJECTS_QUERY, params={"configfile": "settings"}
            )
        except REQUEST_ERRORS as exc:
            _LOGGER.debug("Could not read heater limits: %s", exc)
            return
        settings = (
            data.get("result", {}).get("status", {}).get("configfile", {}).get("settings", {})
        )
        apply_config_limits(self._data, settings, self._objects)

    async def _discover_webcams(self) -> None:
        """Use the first real camera from /server/webcams/list (best effort)."""
        try:
            data = await self._get(ENDPOINT_WEBCAMS_LIST)
        except REQUEST_ERRORS as exc:
            _LOGGER.debug("Could not discover webcams: %s", exc)
            return
        webcams = data.get("result", {}).get("webcams", [])
        enabled = [w for w in webcams if isinstance(w, dict) and w.get("enabled", True)]
        if not enabled:
            _LOGGER.debug("No enabled webcams found via /server/webcams/list")
            return
        real = [
            w
            for w in enabled
            if not any(
                token in f"{w.get('name', '')} {w.get('location', '')}".lower()
                for token in _SCREEN_MIRROR_WEBCAM_TOKENS
            )
        ]
        cam = (real or enabled)[0]
        self._webcam_info = cam
        if cam.get("stream_url"):
            self._camera_stream_url = self.resolve_url(cam["stream_url"])
        if cam.get("snapshot_url"):
            self._camera_snapshot_url = self.resolve_url(cam["snapshot_url"])
        _LOGGER.debug(
            "Discovered webcam '%s': stream=%s snapshot=%s",
            cam.get("name", "unknown"),
            self.camera_stream_url,
            self.camera_snapshot_url,
        )

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Reconnect with exponential back-off until stopped."""
        while self._should_reconnect:
            try:
                await self._ws_connect()
            except asyncio.CancelledError:
                break
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError, MoonrakerError) as exc:
                _LOGGER.warning(
                    "Sovol SV08 WebSocket connection lost (%s). Reconnecting in %ds.",
                    exc,
                    self._reconnect_delay,
                )
            except Exception:  # noqa: BLE001 - keep reconnecting whatever happened
                _LOGGER.exception(
                    "Unexpected Sovol SV08 WebSocket error. Reconnecting in %ds.",
                    self._reconnect_delay,
                )
            self._ws = None
            self._connected = False
            if not self._should_reconnect:
                break
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, RECONNECT_MAX_DELAY)

    async def _ws_connect(self) -> None:
        _LOGGER.debug("Connecting to Sovol SV08 WebSocket: %s", self.ws_url)
        async with self._session.ws_connect(
            self.ws_url,
            headers=self.headers,
        ) as ws:
            self._ws = ws
            self._connected = True
            _LOGGER.info("Sovol SV08 WebSocket connected (%s)", self.host)
            await self._subscribe_objects()
            # A connection that came up resets the back-off, however it ends.
            self._reconnect_delay = RECONNECT_MIN_DELAY
            while True:
                # TimeoutError here is handled by _ws_loop as a lost connection.
                msg = await ws.receive(timeout=WS_IDLE_TIMEOUT)
                if msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await self.handle_ws_message(json.loads(msg.data))
                    except (ValueError, KeyError, TypeError) as exc:
                        _LOGGER.debug("Ignoring malformed WebSocket message: %s", exc)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.warning("WebSocket error: %s", ws.exception())
                    break
        self._ws = None
        self._connected = False

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _subscribe_objects(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": WS_METHOD_SUBSCRIBE,
            "params": {"objects": dict.fromkeys(self._objects.all_objects())},
            "id": self._next_id(),
        }
        await self._ws.send_str(json.dumps(payload))

    async def handle_ws_message(self, msg: dict[str, Any]) -> None:
        """Dispatch one incoming WebSocket message."""
        if "result" in msg:
            result = msg["result"]
            if isinstance(result, dict) and "status" in result:
                await self._process_status(result["status"])
            return

        method = msg.get("method", "")
        params = msg.get("params") or []

        if method == WS_NOTIFY_STATUS_UPDATE and params:
            await self._process_status(params[0])
        elif method == WS_NOTIFY_KLIPPY_READY:
            self._data.klipper_state = KLIPPER_READY
            self._data.klipper_message = "Klipper ready"
            # Objects can change after a Klipper restart (e.g. a new fan).
            await self._resubscribe()
        elif method in (WS_NOTIFY_KLIPPY_SHUTDOWN, WS_NOTIFY_KLIPPY_DISCONNECTED):
            self._data.klipper_state = KLIPPER_SHUTDOWN
            self._data.klipper_message = "Klipper disconnected"
            await self._notify_callbacks()
        elif method == WS_NOTIFY_FILELIST_CHANGED:
            await self.fetch_file_list()
            await self._notify_callbacks()
        elif method == WS_NOTIFY_HISTORY_CHANGED:
            await self.fetch_state()
            await self.fetch_file_list()

    async def _resubscribe(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._subscribe_objects()
        await self._notify_callbacks()

    async def _process_status(self, status: dict[str, Any]) -> None:
        changed = apply_status(self._data, status, self._objects)
        if "print_stats" in status:
            await self._refresh_metadata_if_needed()
        if changed:
            await self._notify_callbacks()

    async def _notify_callbacks(self) -> None:
        for cb in list(self._callbacks):
            try:
                result = cb(self._data)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001 - one bad listener must not stop updates
                _LOGGER.exception("Error in Sovol SV08 data callback")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        timeout: float = HTTP_TIMEOUT,
    ) -> dict:
        async with self._session.request(
            method,
            f"{self.base_url}{endpoint}",
            headers=self.headers,
            params=params,
            json=json_body,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status in (401, 403):
                raise MoonrakerAuthError(await _error_message(resp))
            if resp.status >= 400:
                raise MoonrakerError(await _error_message(resp))
            if resp.status == 204 or resp.content_length == 0:
                return {}
            try:
                return await resp.json(content_type=None)
            except ValueError as exc:
                # e.g. an HTML page from nginx while Moonraker starts
                raise MoonrakerError(f"Unexpected reply from {endpoint}") from exc

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        return await self._request("GET", endpoint, params=params)

    async def _post(
        self, endpoint: str, data: dict | None = None, timeout: float = HTTP_TIMEOUT
    ) -> dict:
        return await self._request("POST", endpoint, json_body=data or {}, timeout=timeout)

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    async def fetch_state(self) -> None:
        """Refresh all subscribed objects via HTTP (init and fallback poll)."""
        params = dict.fromkeys(self._objects.all_objects(), "")
        data = await self._get(ENDPOINT_PRINTER_OBJECTS_QUERY, params=params)
        await self._process_status(data.get("result", {}).get("status", {}))

    async def fetch_file_list(self) -> None:
        try:
            data = await self._get(ENDPOINT_FILES_LIST, params={"root": "gcodes"})
        except REQUEST_ERRORS as exc:
            _LOGGER.debug("Could not fetch file list: %s", exc)
            return
        self._data.available_files = parse_file_list(data.get("result", []))

    async def _refresh_metadata_if_needed(self) -> None:
        """Fetch slicer metadata once per printed file."""
        filename = self._data.print_stats.filename
        if filename == self._metadata_for:
            return
        if not filename:
            self._metadata_for = filename
            self._data.file_metadata = FileMetadata()
            return
        try:
            data = await self._get(ENDPOINT_FILES_METADATA, params={"filename": filename})
        except REQUEST_ERRORS as exc:
            _LOGGER.debug("No metadata for %s: %s", filename, exc)
            self._data.file_metadata = FileMetadata()
            return
        self._data.file_metadata = parse_file_metadata(data.get("result", {}))
        self._metadata_for = filename

    # ------------------------------------------------------------------
    # Printer control commands
    # ------------------------------------------------------------------

    async def pause_print(self) -> None:
        await self._post(ENDPOINT_PRINT_PAUSE)

    async def resume_print(self) -> None:
        await self._post(ENDPOINT_PRINT_RESUME)

    async def cancel_print(self) -> None:
        await self._post(ENDPOINT_PRINT_CANCEL)

    async def emergency_stop(self) -> None:
        await self._post(ENDPOINT_EMERGENCY_STOP)

    async def restart_klipper(self) -> None:
        await self._post(ENDPOINT_PRINTER_RESTART)

    async def firmware_restart(self) -> None:
        """Restart Klipper and its MCUs (needed after an MCU shutdown)."""
        await self._post(ENDPOINT_FIRMWARE_RESTART)

    async def start_print(self, filename: str) -> None:
        await self._post(ENDPOINT_PRINT_START, {"filename": filename})

    async def execute_gcode(self, script: str, timeout: float = LONG_GCODE_TIMEOUT) -> None:
        """Send a G-code command or macro and wait for it to finish.

        Klipper queues commands behind moves already running (homing, QGL),
        so even a temperature change can take minutes to be answered.
        """
        await self._post(ENDPOINT_GCODE_SCRIPT, {"script": script}, timeout=timeout)

    async def home_axes(self, axes: str = "") -> None:
        """Home 'X', 'Y', 'Z' or, when empty, all axes."""
        await self.execute_gcode(f"G28 {axes}".strip(), timeout=LONG_GCODE_TIMEOUT)

    def _home_first(self) -> list[str]:
        return [] if self._data.is_homed else ["G28"]

    async def quad_gantry_level(self) -> None:
        """Level the gantry, homing first if needed and re-homing Z after."""
        script = self._home_first() + ["QUAD_GANTRY_LEVEL", "G28 Z"]
        await self.execute_gcode("\n".join(script), timeout=LONG_GCODE_TIMEOUT)

    async def bed_mesh_calibrate(self) -> None:
        """Mesh the bed, leveling the gantry first if it isn't leveled yet.

        With Sovol's eddy upgrade, the printer's own BED_MESH_CALIBRATE macro
        already levels the gantry (and calibrates Z offset), so it isn't
        done twice. The stock SV08 macro only heats the bed and homes.
        """
        script = self._home_first()
        if (
            self._objects.has_qgl
            and self._objects.eddy_probe is None
            and not self._data.qgl_applied
        ):
            script += ["QUAD_GANTRY_LEVEL", "G28 Z"]
        script += ["BED_MESH_CALIBRATE"]
        await self.execute_gcode("\n".join(script), timeout=LONG_GCODE_TIMEOUT)

    async def z_offset_calibrate(self) -> None:
        """Run Sovol's Z_OFFSET_CALIBRATION (the probe-to-nozzle offset)."""
        if not self._objects.has_z_offset_calibration:
            raise ValueError("This printer has no Z_OFFSET_CALIBRATION command")
        script = self._home_first() + ["Z_OFFSET_CALIBRATION"]
        await self.execute_gcode("\n".join(script), timeout=LONG_GCODE_TIMEOUT)

    async def set_bed_temperature(self, temp: float) -> None:
        _check_target("Bed", temp, self._data.heater_bed.max_temp)
        await self.execute_gcode(f"M140 S{temp:g}")

    async def set_nozzle_temperature(self, temp: float) -> None:
        _check_target("Nozzle", temp, self._data.extruder.max_temp)
        await self.execute_gcode(f"M104 S{temp:g}")

    async def set_fan_speed(self, speed_pct: float) -> None:
        """Set the part-cooling fan (0-100 %)."""
        _check_range("Fan speed", speed_pct, 0, 100)
        await self.execute_gcode(f"M106 S{round(speed_pct / 100 * 255)}")

    async def set_extra_fan_speed(self, fan_key: str, speed_pct: float) -> None:
        """Set a fan_generic (e.g. an exhaust fan) to 0-100 %."""
        if fan_key not in self._objects.fan_generics:
            raise ValueError(f"{fan_key} is not a controllable fan")
        _check_range("Fan speed", speed_pct, 0, 100)
        await self.execute_gcode(
            f"SET_FAN_SPEED FAN={object_name(fan_key)} SPEED={speed_pct / 100:.2f}"
        )

    async def set_speed_factor(self, speed_pct: float) -> None:
        _check_range("Speed factor", speed_pct, 10, 300)
        await self.execute_gcode(f"M220 S{round(speed_pct)}")

    async def set_flow_rate(self, flow_pct: float) -> None:
        _check_range("Flow rate", flow_pct, 50, 150)
        await self.execute_gcode(f"M221 S{round(flow_pct)}")

    async def set_light(self, on: bool) -> None:
        """Switch the LED bar (output_pin main_led on a stock SV08)."""
        light = self._objects.light
        if light is None:
            raise ValueError("This printer has no light Klipper can control")
        name = object_name(light)
        value = 1 if on else 0
        if light.startswith("output_pin "):
            await self.execute_gcode(f"SET_PIN PIN={name} VALUE={value}")
        else:
            await self.execute_gcode(
                f"SET_LED LED={name} RED={value} GREEN={value} BLUE={value} WHITE={value}"
            )
        self._data.light_on = on

    async def set_chamber_heater_temperature(self, heater_key: str, temp: float) -> None:
        """Set a heater_generic target; 0 turns it off."""
        heater = self._data.chamber_heaters.get(heater_key)
        if heater is None:
            raise ValueError(f"Unknown heater {heater_key}")
        if temp and not heater.min_temp <= temp <= heater.max_temp:
            raise ValueError(
                f"Target must be 0 (off) or between {heater.min_temp:g} and "
                f"{heater.max_temp:g} °C"
            )
        await self.execute_gcode(
            f"SET_HEATER_TEMPERATURE HEATER={object_name(heater_key)} TARGET={temp:g}"
        )

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def test_connection(
        session: aiohttp.ClientSession,
        host: str,
        port: int = 80,
        api_key: str | None = None,
    ) -> str | None:
        """Check the Moonraker API: None when reachable, else an error key.

        Error keys match the config flow: "invalid_auth" or "cannot_connect".
        """
        headers = {"X-Api-Key": api_key} if api_key else {}
        try:
            async with session.get(
                f"{build_base_url(host, port)}{ENDPOINT_SERVER_INFO}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status in (401, 403):
                    return "invalid_auth"
                return None if resp.status == 200 else "cannot_connect"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return "cannot_connect"


async def _error_message(resp: aiohttp.ClientResponse) -> str:
    """Moonraker's error text, e.g. 'Must home axis first'."""
    try:
        body = await resp.json(content_type=None)
        message = body.get("error", {}).get("message")
        if message:
            return str(message)
    except (ValueError, AttributeError, aiohttp.ContentTypeError):
        pass
    return f"HTTP {resp.status}"


def _check_range(label: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low:g} and {high:g}, got {value:g}")


def _check_target(label: str, temp: float, max_temp: float) -> None:
    if not 0 <= temp <= max_temp:
        raise ValueError(f"{label} target must be between 0 and {max_temp:g} °C")
