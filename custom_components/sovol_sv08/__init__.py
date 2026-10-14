"""Sovol SV08 Home Assistant integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, UnitOfTemperature
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_HOST,
    CONF_TEMPERATURE_UNIT,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
)
from .coordinator import SovolDataUpdateCoordinator
from .frontend import async_register_frontend
from .pysovol import REQUEST_ERRORS, SovolClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# hass.data key: options each loaded entry was set up with
_OPTIONS_SEEN = f"{DOMAIN}_options"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

SERVICE_EXECUTE_GCODE = "execute_gcode"
SERVICE_SET_BED_TEMPERATURE = "set_bed_temperature"
SERVICE_SET_NOZZLE_TEMPERATURE = "set_nozzle_temperature"
SERVICE_START_PRINT = "start_print"

_ENTRY = {vol.Required("config_entry_id"): cv.string}
_TEMPERATURE = vol.All(vol.Coerce(float), vol.Range(min=0, max=350))

SERVICES = {
    SERVICE_EXECUTE_GCODE: (
        vol.Schema({**_ENTRY, vol.Required("script"): cv.string}),
        lambda client, data: client.execute_gcode(data["script"]),
    ),
    SERVICE_SET_BED_TEMPERATURE: (
        vol.Schema({**_ENTRY, vol.Required("temperature"): _TEMPERATURE}),
        lambda client, data: client.set_bed_temperature(data["temperature"]),
    ),
    SERVICE_SET_NOZZLE_TEMPERATURE: (
        vol.Schema({**_ENTRY, vol.Required("temperature"): _TEMPERATURE}),
        lambda client, data: client.set_nozzle_temperature(data["temperature"]),
    ),
    SERVICE_START_PRINT: (
        vol.Schema({**_ENTRY, vol.Required("filename"): cv.string}),
        lambda client, data: client.start_print(data["filename"]),
    ),
}


def _get_client(hass: HomeAssistant, call: ServiceCall) -> SovolClient:
    entry_id = call.data["config_entry_id"]
    coordinator: SovolDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
    if coordinator is None:
        raise ServiceValidationError(f"No Sovol SV08 is set up with entry '{entry_id}'")
    return coordinator.client


def _register_services(hass: HomeAssistant) -> None:
    """Register the integration's services (once, for all printers)."""
    for name, (schema, action) in SERVICES.items():

        async def handle(call: ServiceCall, action=action) -> None:
            try:
                await action(_get_client(hass, call), call.data)
            except ValueError as exc:
                raise ServiceValidationError(str(exc)) from exc
            except REQUEST_ERRORS as exc:
                raise HomeAssistantError(f"Sovol SV08 rejected the request: {exc}") from exc

        hass.services.async_register(DOMAIN, name, handle, schema)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled dashboard card."""
    try:
        await async_register_frontend(hass)
    except Exception:  # noqa: BLE001 - the card is optional; never block setup
        _LOGGER.exception("Could not register the Sovol SV08 dashboard card")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Sovol SV08 from a config entry."""
    coordinator = SovolDataUpdateCoordinator(hass, entry)
    if not await coordinator.async_setup():
        # Home Assistant retries with back-off, e.g. while the printer boots.
        raise ConfigEntryNotReady(f"Cannot reach the Sovol SV08 at {entry.data[CONF_HOST]}")

    # Stops the WebSocket on unload, and if anything below fails.
    entry.async_on_unload(coordinator.async_shutdown)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    if not hass.services.has_service(DOMAIN, SERVICE_EXECUTE_GCODE):
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _apply_temperature_unit(hass, entry)
    hass.data.setdefault(_OPTIONS_SEEN, {})[entry.entry_id] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _apply_temperature_unit(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Show every printer temperature in the unit chosen in the options.

    Home Assistant converts temperatures to its own unit system (°F on US
    installs). Setting the unit in the entity registry options overrides that
    per entity; "Home Assistant default" removes the override again.
    """
    choice = entry.options.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT)
    unit = {
        TEMP_UNIT_CELSIUS: UnitOfTemperature.CELSIUS,
        TEMP_UNIT_FAHRENHEIT: UnitOfTemperature.FAHRENHEIT,
    }.get(choice)
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain not in ("sensor", "number"):
            continue
        device_class = reg_entry.device_class or reg_entry.original_device_class
        if device_class != "temperature":
            continue
        options = dict(reg_entry.options.get(reg_entry.domain, {}))
        if unit is None:
            if "unit_of_measurement" not in options:
                continue
            options.pop("unit_of_measurement")
        elif options.get("unit_of_measurement") == unit:
            continue
        else:
            options["unit_of_measurement"] = unit
        registry.async_update_entity_options(reg_entry.entity_id, reg_entry.domain, options)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options change.

    Reconfigure and re-auth already reload the entry themselves, so data-only
    updates are ignored here instead of reloading twice.
    """
    if hass.data.get(_OPTIONS_SEEN, {}).get(entry.entry_id) != dict(entry.options):
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    hass.data[DOMAIN].pop(entry.entry_id, None)
    hass.data.get(_OPTIONS_SEEN, {}).pop(entry.entry_id, None)
    if not hass.data[DOMAIN]:
        for name in SERVICES:
            hass.services.async_remove(DOMAIN, name)
    return True
