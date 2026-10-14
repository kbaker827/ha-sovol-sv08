"""Switch platform for the Sovol SV08: the LED light bar."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import SovolEntity
from .pysovol import REQUEST_ERRORS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the light switch when Klipper has a light object."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.client.objects.light:
        async_add_entities([LightSwitch(coordinator)])


class LightSwitch(SovolEntity, SwitchEntity):
    """The printer's LED bar (``output_pin main_led`` on a stock SV08).

    The state comes from Klipper, so it is right even when the light is
    switched from Mainsail or the touchscreen.
    """

    _attr_translation_key = "light"
    _attr_icon = "mdi:led-strip-variant"

    def __init__(self, coordinator: SovolDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "light")

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.light_on

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.is_ready

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, on: bool) -> None:
        try:
            await self.coordinator.client.set_light(on)
        except REQUEST_ERRORS as exc:
            raise HomeAssistantError(f"Could not switch the light: {exc}") from exc
        self.async_write_ha_state()
