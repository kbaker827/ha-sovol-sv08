"""Base entity for the Sovol SV08 integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import SovolDataUpdateCoordinator


def device_info(coordinator: SovolDataUpdateCoordinator) -> DeviceInfo:
    """The one device every entity of a config entry belongs to."""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.entry.entry_id)},
        name=coordinator.entry.title,
        manufacturer=MANUFACTURER,
        model=MODEL,
        sw_version=coordinator.client.data.firmware_version or None,
        configuration_url=coordinator.client.base_url,
    )


class SovolEntity(CoordinatorEntity[SovolDataUpdateCoordinator]):
    """Base class: device info and a unique ID of ``<entry_id>_<key>``.

    The dashboard card finds entities by that key (see frontend.py), so keys
    must stay stable.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: SovolDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.unique_prefix}_{key}"
        self._attr_device_info = device_info(coordinator)

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
