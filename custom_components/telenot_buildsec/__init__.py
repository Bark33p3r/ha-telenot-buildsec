"""Telenot BuildSec integration: control a Telenot complex alarm panel via its LAN keypad interface."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_KEYS,
    CONF_ADDRESS,
    CONF_BEDIENTEILCODE,
    CONF_FREISCHALTCODE,
    DOMAIN,
    SERVICE_SEND_KEYS,
)
from .hub import TelenotHub
from .telenot import BuildSecError, S2RemoteBusy

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]

type TelenotConfigEntry = ConfigEntry[TelenotHub]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SEND_KEYS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_KEYS): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _send_keys(call: ServiceCall) -> None:
        entries = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED
        ]
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if entry_id:
            entries = [e for e in entries if e.entry_id == entry_id]
        if len(entries) != 1:
            raise ServiceValidationError(
                "Bitte config_entry_id angeben (keine oder mehrere Anlagen gefunden)"
            )
        hub: TelenotHub = entries[0].runtime_data
        if not hub.available:
            raise HomeAssistantError("Keine Verbindung zur Alarmanlage")
        await hub.async_press(call.data[ATTR_KEYS])

    hass.services.async_register(DOMAIN, SERVICE_SEND_KEYS, _send_keys, schema=SEND_KEYS_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TelenotConfigEntry) -> bool:
    hub = TelenotHub(
        hass,
        entry.entry_id,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_FREISCHALTCODE],
        entry.data[CONF_BEDIENTEILCODE],
        entry.data.get(CONF_ADDRESS, 0),
    )
    try:
        await asyncio.wait_for(hub.async_first_connect(), 90)
    except S2RemoteBusy as err:
        raise ConfigEntryNotReady(f"Fernzugriff belegt: {err}") from err
    except BuildSecError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except (OSError, asyncio.TimeoutError, Exception) as err:  # noqa: BLE001
        await hub.client.disconnect()
        raise ConfigEntryNotReady(f"Verbindung fehlgeschlagen: {err}") from err

    entry.runtime_data = hub
    hub.start(entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: TelenotConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TelenotConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
