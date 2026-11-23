"""Display text sensors for Telenot BuildSec."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .const import VERSION
from .entity import TelenotEntity
from .hub import TelenotHub


async def async_setup_entry(
    hass: HomeAssistant, entry: TelenotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        [
            TelenotDisplaySensor(hub, 1),
            TelenotDisplaySensor(hub, 2),
            TelenotBuzzerSensor(hub),
            TelenotConnectionSensor(hub),
        ]
    )


class TelenotDisplaySensor(TelenotEntity, SensorEntity):
    _attr_icon = "mdi:monitor"

    def __init__(self, hub: TelenotHub, line: int) -> None:
        super().__init__(hub, f"display_line{line}")
        self._line = line
        self._attr_translation_key = f"display_line{line}"

    @property
    def native_value(self) -> str:
        st = self.hub.state
        return st.line1 if self._line == 1 else st.line2


class TelenotBuzzerSensor(TelenotEntity, SensorEntity):
    _attr_icon = "mdi:volume-high"
    _attr_translation_key = "buzzer"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: TelenotHub) -> None:
        super().__init__(hub, "buzzer")

    @property
    def native_value(self) -> str:
        return self.hub.state.buzzer


class TelenotConnectionSensor(TelenotEntity, SensorEntity):
    """Always available: shows connection state / last error."""

    _attr_icon = "mdi:lan-connect"
    _attr_translation_key = "connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: TelenotHub) -> None:
        super().__init__(hub, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        if self.hub.available:
            return "connected"
        if not self.hub.enabled:
            return "released"
        return "disconnected"

    @property
    def extra_state_attributes(self) -> dict:
        st = self.hub.state
        return {
            "last_error": self.hub.last_error or st.last_error,
            "firmware": st.firmware,
            "legacy_mode": st.legacy_mode,
            "version": VERSION,
        }
