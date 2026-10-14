"""Sensor platform for the Sovol SV08 integration."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import SovolEntity
from .pysovol.models import HeaterData, SovolPrinterData
from .pysovol.parser import object_label, object_name

PRINT_STATES = ["standby", "printing", "paused", "complete", "error", "cancelled"]


@dataclass(frozen=True, kw_only=True)
class SovolSensorEntityDescription(SensorEntityDescription):
    """Sensor description reading its value from SovolPrinterData."""

    value_fn: Callable[[SovolPrinterData], Any]
    available_fn: Callable[[SovolPrinterData], bool] = field(default=lambda data: True)
    attrs_fn: Callable[[SovolPrinterData], dict[str, Any]] = field(default=lambda data: {})


def _during_print(data: SovolPrinterData) -> bool:
    return data.is_active


def _eta(data: SovolPrinterData) -> datetime | None:
    remaining = data.time_remaining
    if remaining is None:
        return None
    # Whole minutes, so the state doesn't change with every update.
    eta = dt_util.utcnow() + timedelta(seconds=remaining)
    return eta.replace(second=0, microsecond=0)


def _heater_attrs(heater: HeaterData) -> dict[str, Any]:
    return {
        "target": heater.target,
        "power": round(heater.power, 2),
        "max_temp": heater.max_temp,
    }


def _temperature(key: str, value_fn, attrs_fn=None, icon: str = "mdi:thermometer"):
    return SovolSensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon=icon,
        value_fn=value_fn,
        attrs_fn=attrs_fn or (lambda data: {}),
    )


def _percent(key: str, value_fn, icon: str) -> SovolSensorEntityDescription:
    return SovolSensorEntityDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon=icon,
        value_fn=value_fn,
    )


def _duration(key: str, value_fn, icon: str, available_fn=_during_print):
    return SovolSensorEntityDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        icon=icon,
        value_fn=value_fn,
        available_fn=available_fn,
    )


PRINTER_SENSORS: list[SovolSensorEntityDescription] = [
    SovolSensorEntityDescription(
        key="print_state",
        translation_key="print_state",
        device_class=SensorDeviceClass.ENUM,
        options=PRINT_STATES,
        icon="mdi:printer-3d",
        value_fn=lambda data: data.print_stats.state
        if data.print_stats.state in PRINT_STATES
        else None,
    ),
    SovolSensorEntityDescription(
        key="print_progress",
        translation_key="print_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-check",
        value_fn=lambda data: data.print_progress_pct,
        available_fn=_during_print,
    ),
    SovolSensorEntityDescription(
        key="current_layer",
        translation_key="current_layer",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:layers",
        value_fn=lambda data: data.print_stats.current_layer or None,
        available_fn=_during_print,
    ),
    SovolSensorEntityDescription(
        key="total_layers",
        translation_key="total_layers",
        icon="mdi:layers-triple",
        value_fn=lambda data: data.total_layers or None,
        available_fn=_during_print,
    ),
    _duration("print_duration", lambda d: int(d.print_stats.print_duration), "mdi:timer"),
    _duration("total_duration", lambda d: int(d.print_stats.total_duration), "mdi:timer-outline"),
    _duration(
        "time_remaining",
        lambda d: d.time_remaining,
        "mdi:timer-sand",
        available_fn=lambda d: d.is_printing,
    ),
    SovolSensorEntityDescription(
        key="print_eta",
        translation_key="print_eta",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-end",
        value_fn=_eta,
        available_fn=lambda data: data.is_printing and data.time_remaining is not None,
    ),
    SovolSensorEntityDescription(
        key="filament_used",
        translation_key="filament_used",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:tape-measure",
        value_fn=lambda data: round(data.print_stats.filament_used, 1),
        available_fn=_during_print,
    ),
    SovolSensorEntityDescription(
        key="filename",
        translation_key="filename",
        icon="mdi:file-code",
        value_fn=lambda data: data.print_stats.filename or None,
        attrs_fn=lambda data: {
            "slicer": data.file_metadata.slicer,
            "slicer_estimated_time": data.file_metadata.estimated_time,
        },
    ),
    _temperature(
        "nozzle_temperature",
        lambda d: d.extruder.temperature,
        lambda d: _heater_attrs(d.extruder),
        icon="mdi:printer-3d-nozzle-heat",
    ),
    _temperature("nozzle_target", lambda d: d.extruder.target, icon="mdi:thermometer-lines"),
    _temperature(
        "bed_temperature",
        lambda d: d.heater_bed.temperature,
        lambda d: _heater_attrs(d.heater_bed),
        icon="mdi:radiator",
    ),
    _temperature("bed_target", lambda d: d.heater_bed.target, icon="mdi:radiator"),
    _percent("fan_speed", lambda d: d.fan_speed, "mdi:fan"),
    _percent("speed_factor", lambda d: d.speed_factor_pct, "mdi:speedometer"),
    _percent("flow_rate", lambda d: d.flow_rate_pct, "mdi:water-percent"),
    SovolSensorEntityDescription(
        key="klipper_state",
        translation_key="klipper_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
        value_fn=lambda data: data.klipper_state,
        attrs_fn=lambda data: {"message": data.klipper_message},
    ),
    SovolSensorEntityDescription(
        key="toolhead_position",
        translation_key="toolhead_position",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:crosshairs-gps",
        value_fn=lambda data: "X{:.2f} Y{:.2f} Z{:.2f}".format(*data.toolhead.position[:3]),
        attrs_fn=lambda data: {
            "x": round(data.toolhead.position[0], 2),
            "y": round(data.toolhead.position[1], 2),
            "z": round(data.toolhead.position[2], 2),
            "homed_axes": data.toolhead.homed_axes,
        },
    ),
]

PROBE_Z_SENSOR = SovolSensorEntityDescription(
    key="probe_last_z",
    translation_key="probe_last_z",
    entity_category=EntityCategory.DIAGNOSTIC,
    device_class=SensorDeviceClass.DISTANCE,
    native_unit_of_measurement=UnitOfLength.MILLIMETERS,
    suggested_display_precision=3,
    icon="mdi:arrow-collapse-down",
    value_fn=lambda data: data.probe_last_z,
    attrs_fn=lambda data: {"triggered": data.probe_triggered},
)

BED_MESH_SENSOR = SovolSensorEntityDescription(
    key="bed_mesh_profile",
    translation_key="bed_mesh_profile",
    icon="mdi:grid",
    value_fn=lambda data: data.bed_mesh_profile,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sovol SV08 sensors from a config entry."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    objects = coordinator.client.objects

    descriptions = list(PRINTER_SENSORS)
    if objects.has_bed_mesh:
        descriptions.append(BED_MESH_SENSOR)
    if objects.has_probe or objects.eddy_probe:
        descriptions.append(PROBE_Z_SENSOR)
    descriptions += [_temperature_sensor(key) for key in objects.temperature_sensors]
    descriptions += [_extra_fan_sensor(key) for key in objects.extra_fans]
    descriptions += [_chamber_heater_sensor(key) for key in objects.chamber_heaters]

    async_add_entities(SovolSensor(coordinator, desc) for desc in descriptions)


def _temperature_sensor(object_key: str) -> SovolSensorEntityDescription:
    """A Klipper temperature_sensor (MCU, host, chamber, ...)."""
    return SovolSensorEntityDescription(
        key=f"temp_{object_name(object_key)}",
        name=object_label(object_key),
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:thermometer",
        value_fn=lambda data: data.temperature_sensors.get(object_key),
    )


def _extra_fan_sensor(object_key: str) -> SovolSensorEntityDescription:
    """Hotend / controller / exhaust fan speed."""
    return SovolSensorEntityDescription(
        key=f"fan_{object_name(object_key)}",
        name=object_label(object_key),
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:fan",
        value_fn=lambda data: data.extra_fans.get(object_key),
    )


def _chamber_heater_sensor(object_key: str) -> SovolSensorEntityDescription:
    def attrs(data: SovolPrinterData) -> dict[str, Any]:
        heater = data.chamber_heaters.get(object_key)
        if heater is None:
            return {}
        return {**_heater_attrs(heater), "min_temp": heater.min_temp}

    return SovolSensorEntityDescription(
        key=f"heater_{object_name(object_key)}_temperature",
        name=f"{object_label(object_key)} Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:heat-wave",
        value_fn=lambda data: (
            data.chamber_heaters[object_key].temperature
            if object_key in data.chamber_heaters
            else None
        ),
        attrs_fn=attrs,
    )


class SovolSensor(SovolEntity, SensorEntity):
    """A Sovol SV08 sensor driven by its description."""

    entity_description: SovolSensorEntityDescription

    def __init__(
        self,
        coordinator: SovolDataUpdateCoordinator,
        description: SovolSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(
            self.coordinator.data
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self.coordinator.data)
