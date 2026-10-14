"""Config flow for the Sovol SV08 integration."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TEMPERATURE_UNIT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    TEMP_UNIT_CELSIUS,
    TEMP_UNIT_FAHRENHEIT,
    TEMP_UNIT_SYSTEM,
)
from .pysovol import SovolClient

DEFAULT_TITLE = "Sovol SV08"


def _connection_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Optional(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_API_KEY, default=defaults.get(CONF_API_KEY) or ""): str,
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    """Trim the host (and a pasted http:// prefix) and blank API keys."""
    host = user_input[CONF_HOST].strip().rstrip("/")
    for scheme in ("http://", "https://"):
        if host.lower().startswith(scheme):
            host = host[len(scheme):]
    return {
        CONF_HOST: host,
        CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
        CONF_API_KEY: user_input.get(CONF_API_KEY, "").strip() or None,
    }


class SovolConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Sovol SV08."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SovolOptionsFlow:
        return SovolOptionsFlow(config_entry)

    async def _connection_error(self, data: dict[str, Any]) -> str | None:
        """None when Moonraker answers, else "cannot_connect" / "invalid_auth"."""
        return await SovolClient.test_connection(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_API_KEY],
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the printer's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            await self.async_set_unique_id(f"{data[CONF_HOST]}:{data[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            if (error := await self._connection_error(data)) is None:
                title = DEFAULT_TITLE
                if self._async_current_entries():
                    title = f"{DEFAULT_TITLE} ({data[CONF_HOST]})"
                return self.async_create_entry(title=title, data=data)
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address of an existing printer."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            data = _clean(user_input)
            unique_id = f"{data[CONF_HOST]}:{data[CONF_PORT]}"
            if any(
                other.unique_id == unique_id and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")
            if (error := await self._connection_error(data)) is None:
                return self.async_update_reload_and_abort(
                    entry, unique_id=unique_id, data_updates=data
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or dict(entry.data)),
            errors=errors,
        )


    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Moonraker rejected the API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input[CONF_API_KEY].strip() or None}
            if (error := await self._connection_error(data)) is None:
                return self.async_update_reload_and_abort(entry, data_updates=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )


class SovolOptionsFlow(config_entries.OptionsFlow):
    """Polling interval and temperature unit."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Newer Home Assistant has a read-only `config_entry` property that
        # raises when assigned; keep our own reference instead.
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                vol.Optional(
                    CONF_TEMPERATURE_UNIT,
                    default=options.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[TEMP_UNIT_CELSIUS, TEMP_UNIT_FAHRENHEIT, TEMP_UNIT_SYSTEM],
                        translation_key=CONF_TEMPERATURE_UNIT,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
