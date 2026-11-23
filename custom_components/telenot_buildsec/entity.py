"""Base entity for Telenot BuildSec."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, signal_update
from .hub import TelenotHub


class TelenotEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: TelenotHub, key: str) -> None:
        self.hub = hub
        self._attr_unique_id = f"{hub.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.entry_id)},
            manufacturer="Telenot",
            model="complex (BuildSec)",
            name="Alarmanlage",
            sw_version=hub.state.firmware,
        )

    @property
    def available(self) -> bool:
        return self.hub.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_update(self.hub.entry_id), self.async_write_ha_state
            )
        )
