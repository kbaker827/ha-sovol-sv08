"""Select platform for the Sovol SV08: pick a G-code file to print."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import SovolEntity
from .pysovol import REQUEST_ERRORS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sovol SV08 print file select."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PrintFileSelect(coordinator)])


class PrintFileSelect(SovolEntity, SelectEntity):
    """Choosing a file starts printing it."""

    _attr_translation_key = "print_file"
    _attr_icon = "mdi:file-document"

    def __init__(self, coordinator: SovolDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "print_file")

    @property
    def options(self) -> list[str]:
        return list(self.coordinator.data.available_files)

    @property
    def current_option(self) -> str | None:
        """The file being printed, if it is in the list."""
        filename = self.coordinator.data.print_stats.filename
        return filename if filename in self.coordinator.data.available_files else None

    @property
    def available(self) -> bool:
        data = self.coordinator.data
        return super().available and data.is_ready and bool(data.available_files)

    async def async_select_option(self, option: str) -> None:
        if self.coordinator.data.is_active:
            raise ServiceValidationError(
                f"A print is already running. Cancel or finish it before starting {option}."
            )
        if option not in self.coordinator.data.available_files:
            raise ServiceValidationError(f"{option} is not on the printer")
        try:
            await self.coordinator.client.start_print(option)
        except REQUEST_ERRORS as exc:
            raise HomeAssistantError(f"Could not start {option}: {exc}") from exc
        _LOGGER.info("Started print: %s", option)
