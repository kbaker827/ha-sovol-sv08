"""Device automation triggers for the Sovol SV08.

Print started / complete / failed / paused / cancelled appear under the
printer device in the automation editor, so users don't have to listen to
raw bus events.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo

from .const import DOMAIN
from .pysovol.const import (
    EVENT_PRINT_CANCELLED,
    EVENT_PRINT_COMPLETE,
    EVENT_PRINT_FAILED,
    EVENT_PRINT_PAUSED,
    EVENT_PRINT_STARTED,
)

_TRIGGER_EVENT_MAP: dict[str, str] = {
    "print_started": EVENT_PRINT_STARTED,
    "print_complete": EVENT_PRINT_COMPLETE,
    "print_failed": EVENT_PRINT_FAILED,
    "print_paused": EVENT_PRINT_PAUSED,
    "print_cancelled": EVENT_PRINT_CANCELLED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(_TRIGGER_EVENT_MAP)}
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict]:
    """Return the triggers available for a printer device."""
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
        }
        for trigger_type in _TRIGGER_EVENT_MAP
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Listen for the event behind a device trigger."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: _TRIGGER_EVENT_MAP[config[CONF_TYPE]],
            event_trigger.CONF_EVENT_DATA: {"device_id": config[CONF_DEVICE_ID]},
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
