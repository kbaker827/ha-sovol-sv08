"""Diagnostics for the Sovol SV08 integration.

Settings -> Devices & Services -> Sovol SV08 -> Download diagnostics.
The API key is redacted.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY, CONF_HOST, DOMAIN
from .coordinator import SovolDataUpdateCoordinator

_REDACT = {CONF_API_KEY, CONF_HOST, "snapshot_url", "stream_url", "printer_name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    client = coordinator.client
    return async_redact_data({
        "config_entry": dict(entry.data),
        "options": dict(entry.options),
        "connection": {"websocket_connected": client.connected},
        "discovered_objects": asdict(client.objects),
        "camera": {
            "snapshot_url": client.camera_snapshot_url,
            "stream_url": client.camera_stream_url,
            "moonraker_webcam": client.webcam_info,
        },
        "printer": asdict(client.data),
    }, _REDACT)
