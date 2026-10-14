"""Number platform for the Sovol SV08 integration.

Setpoints for the nozzle, bed, fans, speed and flow overrides, plus any
fan_generic or heater_generic the printer's Klipper config defines.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SovolDataUpdateCoordinator
from .entity import SovolEntity
from .pysovol import REQUEST_ERRORS, SovolClient
from .pysovol.models import SovolPrinterData
from .pysovol.parser import object_label, object_name


@dataclass(frozen=True, kw_only=True)
class SovolNumberEntityDescription(NumberEntityDescription):
    """Number description with value/set callbacks."""

    value_fn: Callable[[SovolPrinterData], float | None]
    set_fn: Callable[[SovolClient, float], Coroutine[Any, Any, None]]
    available_fn: Callable[[SovolPrinterData], bool] = field(
        default=lambda data: data.is_ready
    )


def _temperature(
    key: str, max_temp: float, value_fn, set_fn, icon: str, name: str | None = None
) -> SovolNumberEntityDescription:
    return SovolNumberEntityDescription(
        key=key,
        translation_key=None if name else key,
        name=name,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=0,
        native_max_value=max_temp,
        native_step=1,
        mode=NumberMode.BOX,
        icon=icon,
        value_fn=value_fn,
        set_fn=set_fn,
    )


def _percent(
    key: str, low: float, high: float, value_fn, set_fn, icon: str, name: str | None = None
) -> SovolNumberEntityDescription:
    return SovolNumberEntityDescription(
        key=key,
        translation_key=None if name else key,
        name=name,
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=low,
        native_max_value=high,
        native_step=1,
        mode=NumberMode.SLIDER,
        icon=icon,
        value_fn=value_fn,
        set_fn=set_fn,
    )


def _descriptions(client: SovolClient) -> list[SovolNumberEntityDescription]:
    data = client.data
    objects = client.objects
    descriptions = [
        _temperature(
            "nozzle_target_setpoint",
            data.extruder.max_temp,
            lambda d: d.extruder.target,
            lambda c, v: c.set_nozzle_temperature(v),
            "mdi:printer-3d-nozzle-heat",
        ),
        _temperature(
            "bed_target_setpoint",
            data.heater_bed.max_temp,
            lambda d: d.heater_bed.target,
            lambda c, v: c.set_bed_temperature(v),
            "mdi:radiator",
        ),
        _percent(
            "fan_speed_setpoint", 0, 100,
            lambda d: d.fan_speed, lambda c, v: c.set_fan_speed(v), "mdi:fan",
        ),
        _percent(
            "speed_factor_setpoint", 10, 300,
            lambda d: d.speed_factor_pct, lambda c, v: c.set_speed_factor(v), "mdi:speedometer",
        ),
        _percent(
            "flow_rate_setpoint", 50, 150,
            lambda d: d.flow_rate_pct, lambda c, v: c.set_flow_rate(v), "mdi:water-percent",
        ),
    ]
    for key in objects.fan_generics:
        descriptions.append(
            _percent(
                f"fan_{object_name(key)}_setpoint", 0, 100,
                lambda d, k=key: d.extra_fans.get(k),
                lambda c, v, k=key: c.set_extra_fan_speed(k, v),
                "mdi:fan",
                name=f"{object_label(key)} Speed",
            )
        )
    for key in objects.chamber_heaters:
        heater = data.chamber_heaters.get(key)
        descriptions.append(
            _temperature(
                f"heater_{object_name(key)}_target",
                heater.max_temp if heater else 80,
                lambda d, k=key: d.chamber_heaters[k].target if k in d.chamber_heaters else None,
                lambda c, v, k=key: c.set_chamber_heater_temperature(k, v),
                "mdi:heat-wave",
                name=f"{object_label(key)} Target",
            )
        )
    return descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Sovol SV08 number entities from a config entry."""
    coordinator: SovolDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        SovolNumber(coordinator, desc) for desc in _descriptions(coordinator.client)
    )


class SovolNumber(SovolEntity, NumberEntity):
    """A Sovol SV08 setpoint."""

    entity_description: SovolNumberEntityDescription

    def __init__(
        self,
        coordinator: SovolDataUpdateCoordinator,
        description: SovolNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(
            self.coordinator.data
        )

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.entity_description.set_fn(self.coordinator.client, value)
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc
        except REQUEST_ERRORS as exc:
            raise HomeAssistantError(
                f"Could not set {self.entity_description.key}: {exc}"
            ) from exc
