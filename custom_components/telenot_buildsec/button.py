"""Switch function keys (Schaltfunktionen) as buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .entity import TelenotEntity
from .hub import TelenotHub


async def async_setup_entry(
    hass: HomeAssistant, entry: TelenotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        TelenotSchaltfunktion(hub, i) for i, sf in enumerate(hub.state.schaltfunktionen) if sf.label
    )


class TelenotSchaltfunktion(TelenotEntity, ButtonEntity):
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(self, hub: TelenotHub, index: int) -> None:
        super().__init__(hub, f"schaltfunktion_{index + 1}")
        self._index = index
        self._attr_name = hub.state.schaltfunktionen[index].label

    @property
    def extra_state_attributes(self) -> dict:
        sf = self.hub.state.schaltfunktionen[self._index]
        return {
            "active": sf.active,
            "status": sf.text_active if sf.active else sf.text_passive,
            "index": self._index + 1,
        }

    async def async_press(self) -> None:
        if not self.hub.available:
            raise HomeAssistantError("Keine Verbindung zur Alarmanlage")
        await self.hub.client.toggle_schaltfunktion(self._index + 1)
