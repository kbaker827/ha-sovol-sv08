"""Binary sensor platform for the Sovol SV08 integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import SovolEntity
from .pysovol.models import SovolPrinterData
from .pysovol.parser import object_label, object_name


@dataclass(frozen=True, kw_only=True)
class SovolBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Binary sensor description reading from SovolPrinterData."""

    value_fn: Callable[[SovolPrinterData], bool | None]
    attrs_fn: Callable[[SovolPrinterData], dict[str, Any]] = field(default=lambda data: {})


PRINTER_BINARY_SENSORS: list[SovolBinarySensorEntityDescription] = [
    SovolBinarySensorEntityDescription(
        key="printer_ready",
        translation_key="printer_ready",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: data.is_ready,
    ),
    SovolBinarySensorEntityDescription(
        key="is_printing",
        translation_key="is_printing",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:printer-3d-nozzle",
        value_fn=lambda data: data.is_printing,
    ),
    SovolBinarySensorEntityDescription(
        key="is_paused",
        translation_key="is_paused",
        icon="mdi:pause-circle",
        value_fn=lambda data: data.is_paused,
    ),
    SovolBinarySensorEntityDescription(
        key="has_error",
        translation_key="has_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: data.has_error,
        attrs_fn=lambda data: {"klipper_message": data.klipper_message},
    ),
]

QGL_SENSOR = SovolBinarySensorEntityDescription(
    key="qgl_applied",
    translation_key="qgl_applied",
    icon="mdi:axis-z-rotate-clockwise",
    value_fn=lambda data: data.qgl_applied,
)


def _filament_sensor(object_key: str) -> SovolBinarySensorEntityDescription:
    """On (problem) when the runout sensor does NOT detect filament."""

    def value(data: SovolPrinterData) -> bool | None:
        sensor = data.filament_sensors.get(object_key)
        if sensor is None:
            return None
        # A disabled sensor's last reading is stale; don't report a runout.
        return sensor.enabled and not sensor.filament_detected

    def attrs(data: SovolPrinterData) -> dict[str, Any]:
        sensor = data.filament_sensors.get(object_key)
        if sensor is None:
            return {}
        return {"enabled": sensor.enabled, "filament_detected": sensor.filament_detected}

    label = object_label(object_key)
    return SovolBinarySensorEntityDescription(
        key=f"filament_{object_name(object_key)}",
        name=label if "filament" in label.lower() else f"{label} Filament",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:printer-3d-nozzle-alert",
        value_fn=value,
        attrs_fn=attrs,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sovol SV08 binary sensors from a config entry."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    objects = coordinator.client.objects

    descriptions = list(PRINTER_BINARY_SENSORS)
    if objects.has_qgl:
        descriptions.append(QGL_SENSOR)
    descriptions += [_filament_sensor(key) for key in objects.filament_sensors]

    async_add_entities(SovolBinarySensor(coordinator, desc) for desc in descriptions)


class SovolBinarySensor(SovolEntity, BinarySensorEntity):
    """A Sovol SV08 binary sensor driven by its description."""

    entity_description: SovolBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: SovolDataUpdateCoordinator,
        description: SovolBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator.data)
