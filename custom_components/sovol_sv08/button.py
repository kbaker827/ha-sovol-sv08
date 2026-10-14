"""Button platform for the Sovol SV08 integration."""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import SovolEntity
from .pysovol import REQUEST_ERRORS, SovolClient
from .pysovol.models import SovolPrinterData
from .pysovol.parser import DiscoveredObjects

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SovolButtonEntityDescription(ButtonEntityDescription):
    """Button description with an async press handler."""

    press_fn: Callable[[SovolClient], Coroutine[Any, Any, None]]
    available_fn: Callable[[SovolPrinterData], bool] = field(default=lambda data: True)
    exists_fn: Callable[[DiscoveredObjects], bool] = field(default=lambda objects: True)
    # Homing and leveling take minutes; run them in the background so the
    # press returns at once. Errors are logged instead of shown.
    long_running: bool = False


def _idle_and_ready(data: SovolPrinterData) -> bool:
    return data.is_ready and not data.is_active


def _home(axes: str, key: str, icon: str) -> SovolButtonEntityDescription:
    return SovolButtonEntityDescription(
        key=key,
        translation_key=key,
        icon=icon,
        press_fn=lambda client: client.home_axes(axes),
        available_fn=_idle_and_ready,
        long_running=True,
    )


BUTTON_DESCRIPTIONS: list[SovolButtonEntityDescription] = [
    SovolButtonEntityDescription(
        key="pause_print",
        translation_key="pause_print",
        icon="mdi:pause-circle",
        press_fn=lambda client: client.pause_print(),
        available_fn=lambda data: data.is_printing,
    ),
    SovolButtonEntityDescription(
        key="resume_print",
        translation_key="resume_print",
        icon="mdi:play-circle",
        press_fn=lambda client: client.resume_print(),
        available_fn=lambda data: data.is_paused,
    ),
    SovolButtonEntityDescription(
        key="cancel_print",
        translation_key="cancel_print",
        icon="mdi:cancel",
        press_fn=lambda client: client.cancel_print(),
        available_fn=lambda data: data.is_active,
    ),
    SovolButtonEntityDescription(
        key="emergency_stop",
        translation_key="emergency_stop",
        icon="mdi:alert-octagon",
        press_fn=lambda client: client.emergency_stop(),
    ),
    _home("", "home_all", "mdi:home"),
    _home("X", "home_x", "mdi:axis-x-arrow"),
    _home("Y", "home_y", "mdi:axis-y-arrow"),
    _home("Z", "home_z", "mdi:axis-z-arrow"),
    SovolButtonEntityDescription(
        key="quad_gantry_level",
        translation_key="quad_gantry_level",
        icon="mdi:axis-z-rotate-clockwise",
        press_fn=lambda client: client.quad_gantry_level(),
        available_fn=_idle_and_ready,
        exists_fn=lambda objects: objects.has_qgl,
        long_running=True,
    ),
    SovolButtonEntityDescription(
        key="bed_mesh_calibrate",
        translation_key="bed_mesh_calibrate",
        icon="mdi:grid",
        press_fn=lambda client: client.bed_mesh_calibrate(),
        available_fn=_idle_and_ready,
        exists_fn=lambda objects: objects.has_bed_mesh,
        long_running=True,
    ),
    SovolButtonEntityDescription(
        key="z_offset_calibrate",
        translation_key="z_offset_calibrate",
        icon="mdi:arrow-expand-vertical",
        press_fn=lambda client: client.z_offset_calibrate(),
        available_fn=_idle_and_ready,
        exists_fn=lambda objects: objects.has_z_offset_calibration,
        long_running=True,
    ),
    SovolButtonEntityDescription(
        key="restart_klipper",
        translation_key="restart_klipper",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:restart",
        press_fn=lambda client: client.restart_klipper(),
    ),
    SovolButtonEntityDescription(
        key="firmware_restart",
        translation_key="firmware_restart",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:restart-alert",
        press_fn=lambda client: client.firmware_restart(),
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sovol SV08 buttons from a config entry."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    objects = coordinator.client.objects
    async_add_entities(
        SovolButton(coordinator, desc)
        for desc in BUTTON_DESCRIPTIONS
        if desc.exists_fn(objects)
    )


class SovolButton(SovolEntity, ButtonEntity):
    """A button that sends a command to the Sovol SV08."""

    entity_description: SovolButtonEntityDescription

    def __init__(
        self,
        coordinator: SovolDataUpdateCoordinator,
        description: SovolButtonEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(
            self.coordinator.data
        )

    async def async_press(self) -> None:
        if self.entity_description.long_running:
            self.hass.async_create_background_task(
                self._run_logged(), f"{DOMAIN} {self.entity_description.key}"
            )
            return
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except REQUEST_ERRORS as exc:
            raise HomeAssistantError(
                f"{self.entity_description.key} failed: {exc}"
            ) from exc

    async def _run_logged(self) -> None:
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except REQUEST_ERRORS as exc:
            _LOGGER.error("Sovol SV08 %s failed: %s", self.entity_description.key, exc)
