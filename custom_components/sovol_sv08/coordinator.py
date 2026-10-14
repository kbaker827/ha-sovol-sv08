"""Data coordinator for the Sovol SV08 integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .pysovol import REQUEST_ERRORS, MoonrakerAuthError, SovolClient, SovolPrinterData
from .pysovol.const import PRINT_STATE_EVENTS

_LOGGER = logging.getLogger(__name__)


class SovolDataUpdateCoordinator(DataUpdateCoordinator[SovolPrinterData]):
    """Keeps Sovol SV08 state, preferring WebSocket push over polling."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.entry = entry
        self.client = SovolClient(
            host=entry.data[CONF_HOST],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
            api_key=entry.data.get(CONF_API_KEY) or None,
            session=async_get_clientsession(hass),
        )
        self._last_print_state: str | None = None

    @property
    def unique_prefix(self) -> str:
        """Prefix of every entity unique ID.

        Uses the config entry ID rather than the host, so changing the
        printer's IP with Reconfigure keeps entities and their history.
        """
        return self.entry.entry_id

    @property
    def printer_name(self) -> str:
        return self.client.data.printer_name

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> bool:
        """Fetch initial data and start the WebSocket.

        Raises ConfigEntryAuthFailed when Moonraker rejects the API key.
        """
        self.client.register_callback(self._async_push_update)
        try:
            ok = await self.client.async_init()
        except MoonrakerAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        if not ok:
            return False
        self._last_print_state = self.client.data.print_stats.state
        self.async_set_updated_data(self.client.data)
        await self.client.async_start()
        return True

    async def async_shutdown(self) -> None:
        self.client.remove_callback(self._async_push_update)
        await self.client.async_stop()
        await super().async_shutdown()

    # ------------------------------------------------------------------
    # Push / poll updates
    # ------------------------------------------------------------------

    async def _async_push_update(self, data: SovolPrinterData) -> None:
        self._fire_print_state_event(data)
        self.async_set_updated_data(data)

    async def _async_update_data(self) -> SovolPrinterData:
        """Fallback: poll via HTTP while the WebSocket is down."""
        if not self.client.connected:
            try:
                await self.client.fetch_state()
            except MoonrakerAuthError as exc:
                raise ConfigEntryAuthFailed(str(exc)) from exc
            except REQUEST_ERRORS as exc:
                raise UpdateFailed(f"HTTP poll failed: {exc}") from exc
        return self.client.data

    # ------------------------------------------------------------------
    # HA event bus
    # ------------------------------------------------------------------

    def _fire_print_state_event(self, data: SovolPrinterData) -> None:
        new_state = data.print_stats.state
        old_state = self._last_print_state
        if new_state == old_state:
            return
        self._last_print_state = new_state
        if old_state == "paused" and new_state == "printing":
            return  # resuming, not a new print
        event_name = PRINT_STATE_EVENTS.get(new_state)
        if event_name is None:
            return

        event_data: dict[str, Any] = {
            "entry_id": self.entry.entry_id,
            "state": new_state,
            "filename": data.print_stats.filename,
        }
        device = _find_device(
            dr.async_get(self.hass), (DOMAIN, self.entry.entry_id), self.entry.entry_id
        )
        if device:
            event_data["device_id"] = device.id
        self.hass.bus.async_fire(event_name, event_data)
        _LOGGER.debug("Fired event %s (state=%s)", event_name, new_state)


def _find_device(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], entry_id: str
) -> dr.DeviceEntry | None:
    """Look up a device by identifier on old and new Home Assistant.

    2026.x replaced async_get_device (which now raises) with
    async_get_device_by_identifier.
    """
    by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if by_identifier is not None:
        return by_identifier(identifier, entry_id)
    return registry.async_get_device(identifiers={identifier})
