"""Connection switch: turn off to release the panel for the BuildSec phone app."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .entity import TelenotEntity
from .hub import TelenotHub


async def async_setup_entry(
    hass: HomeAssistant, entry: TelenotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([TelenotConnectionSwitch(entry.runtime_data)])


class TelenotConnectionSwitch(TelenotEntity, SwitchEntity):
    _attr_icon = "mdi:lan-connect"
    _attr_translation_key = "keep_connected"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: TelenotHub) -> None:
        super().__init__(hub, "keep_connected")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.hub.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.hub.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.hub.async_set_enabled(False)
