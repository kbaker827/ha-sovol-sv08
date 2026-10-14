"""Camera platform for the Sovol SV08 (crowsnest / ustreamer MJPEG webcam)."""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import (
    async_aiohttp_proxy_stream,
    async_get_clientsession,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import device_info
from .pysovol.const import CAMERA_SNAPSHOT_PATH, CAMERA_STREAM_PATH

_LOGGER = logging.getLogger(__name__)

CAMERA_CONNECT_TIMEOUT = 5
CAMERA_READ_TIMEOUT = 10
CAMERA_FRAME_INTERVAL = 2.0
# Upper bound on bytes read from an MJPEG stream while looking for one frame
MJPEG_MAX_FRAME_SEARCH = 5 * 1024 * 1024

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


class _CameraFetchError(Exception):
    """A webcam URL did not return a usable image."""


def _looks_like_image(content_type: str, body: bytes) -> bool:
    return content_type.startswith("image/") or body.startswith(JPEG_SOI)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sovol SV08 camera from a config entry."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SovolCamera(coordinator)])


class SovolCamera(Camera):
    """Webcam discovered through Moonraker.

    Stills come from the snapshot URL, falling back to the stock crowsnest
    paths and to a frame grabbed from the MJPEG stream. The live view proxies
    the MJPEG stream. ``stream_source`` is not implemented so Home Assistant
    doesn't try HLS transcoding, which needs ffmpeg and isn't needed here.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "webcam"
    _attr_supported_features = CameraEntityFeature(0)
    _attr_frame_interval = CAMERA_FRAME_INTERVAL

    def __init__(self, coordinator: SovolDataUpdateCoordinator) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._client = coordinator.client
        self._attr_unique_id = f"{coordinator.unique_prefix}_webcam"
        self._attr_device_info = device_info(coordinator)
        # (kind, url) of the source that last returned an image
        self._working_source: tuple[str, str] | None = None
        self._warned_unavailable = False

    @property
    def is_streaming(self) -> bool:
        return self._coordinator.data.is_ready if self._coordinator.data else False

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "snapshot_url": self._client.camera_snapshot_url,
            "stream_url": self._client.camera_stream_url,
        }

    def _snapshot_candidates(self) -> list[tuple[str, str]]:
        """Ordered (kind, url) pairs to try when fetching a still image."""
        base = self._client.base_url
        candidates = [
            ("snapshot", self._client.camera_snapshot_url),
            ("snapshot", f"{base}{CAMERA_SNAPSHOT_PATH}"),
            ("stream", self._client.camera_stream_url),
            ("stream", f"{base}{CAMERA_STREAM_PATH}"),
        ]
        if self._working_source in candidates:
            candidates.insert(0, self._working_source)
        seen: set[tuple[str, str]] = set()
        return [c for c in candidates if not (c in seen or seen.add(c))]

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(connect=CAMERA_CONNECT_TIMEOUT, total=CAMERA_READ_TIMEOUT)

    async def _fetch_snapshot(self, session: aiohttp.ClientSession, url: str) -> bytes:
        async with session.get(url, headers=self._client.headers, timeout=self._timeout()) as resp:
            if resp.status != 200:
                raise _CameraFetchError(f"HTTP {resp.status}")
            body = await resp.read()
            if not _looks_like_image(resp.content_type, body):
                raise _CameraFetchError(f"not an image (content-type {resp.content_type!r})")
            return body

    async def _fetch_stream_frame(self, session: aiohttp.ClientSession, url: str) -> bytes:
        """Grab the first complete JPEG frame from an MJPEG stream."""
        async with session.get(url, headers=self._client.headers, timeout=self._timeout()) as resp:
            if resp.status != 200:
                raise _CameraFetchError(f"HTTP {resp.status}")
            buffer = b""
            async for chunk in resp.content.iter_chunked(65536):
                buffer += chunk
                start = buffer.find(JPEG_SOI)
                if start != -1:
                    end = buffer.find(JPEG_EOI, start + 2)
                    if end != -1:
                        return buffer[start : end + 2]
                    buffer = buffer[start:]
                if len(buffer) > MJPEG_MAX_FRAME_SEARCH:
                    break
            raise _CameraFetchError("no JPEG frame found in stream")

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a single JPEG image from the webcam."""
        session = async_get_clientsession(self.hass)
        errors: list[str] = []
        for source in self._snapshot_candidates():
            kind, url = source
            try:
                if kind == "snapshot":
                    image = await self._fetch_snapshot(session, url)
                else:
                    image = await self._fetch_stream_frame(session, url)
            except (_CameraFetchError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                errors.append(f"{url}: {str(exc) or type(exc).__name__}")
                continue
            if source != self._working_source:
                _LOGGER.info("Sovol SV08 webcam image source: %s", url)
                self._working_source = source
            self._warned_unavailable = False
            return image

        if not self._warned_unavailable:
            _LOGGER.warning(
                "Could not get an image from the Sovol SV08 webcam. Check that the "
                "camera shows in Mainsail (http://%s/). Tried: %s",
                self._client.host,
                "; ".join(errors),
            )
            self._warned_unavailable = True
        self._working_source = None
        return None

    async def handle_async_mjpeg_stream(
        self, request: web.Request
    ) -> web.StreamResponse | None:
        """Proxy the printer's MJPEG stream; fall back to repeated stills."""
        session = async_get_clientsession(self.hass)
        stream_url = self._client.camera_stream_url
        try:
            resp = await session.get(
                stream_url,
                headers=self._client.headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=CAMERA_CONNECT_TIMEOUT),
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            _LOGGER.debug("MJPEG stream %s unavailable: %s", stream_url, exc)
            return await super().handle_async_mjpeg_stream(request)
        try:
            content_type = resp.headers.get(aiohttp.hdrs.CONTENT_TYPE, "")
            if resp.status != 200 or not content_type.startswith("multipart/"):
                _LOGGER.debug(
                    "MJPEG stream %s returned HTTP %d (%s); using snapshots",
                    stream_url,
                    resp.status,
                    content_type,
                )
                return await super().handle_async_mjpeg_stream(request)
            return await async_aiohttp_proxy_stream(
                self.hass, request, resp.content, content_type
            )
        finally:
            resp.release()
