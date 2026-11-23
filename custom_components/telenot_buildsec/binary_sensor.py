"""Keypad LEDs and zones (Meldebereiche) as binary sensors."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .entity import TelenotEntity
from .hub import TelenotHub

STATUS_LEDS = {
    "alarm": BinarySensorDeviceClass.SAFETY,
    "stoerung": BinarySensorDeviceClass.PROBLEM,
    "betrieb": BinarySensorDeviceClass.POWER,
    "gehtest": None,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: TelenotConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities: list[BinarySensorEntity] = [TelenotStatusLed(hub, key, dc) for key, dc in STATUS_LEDS.items()]
    entities += [TelenotLed(hub, i) for i in range(4)]
    entities += [
        TelenotZone(hub, i) for i, mb in enumerate(hub.state.meldebereiche) if mb.label
    ]
    async_add_entities(entities)


class TelenotStatusLed(TelenotEntity, BinarySensorEntity):
    def __init__(self, hub: TelenotHub, key: str, device_class) -> None:
        super().__init__(hub, f"led_{key}")
        self._key = key
        self._attr_translation_key = f"led_{key}"
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool:
        return getattr(self.hub.state, self._key)

    @property
    def extra_state_attributes(self) -> dict:
        return {"blinking": getattr(self.hub.state, f"{self._key}_blinking")}


class TelenotLed(TelenotEntity, BinarySensorEntity):
    """One of the 4 freely configurable keypad LEDs (e.g. "Extern scharf")."""

    _attr_icon = "mdi:led-on"

    def __init__(self, hub: TelenotHub, index: int) -> None:
        super().__init__(hub, f"led_{index + 1}")
        self._index = index
        led = hub.state.leds[index]
        label = " ".join(t for t in (led.label, led.label2) if t)
        if label:
            self._attr_name = f"LED {index + 1} {label}"
        else:
            self._attr_translation_key = "led_n"
            self._attr_translation_placeholders = {"n": str(index + 1)}

    @property
    def is_on(self) -> bool:
        return self.hub.state.leds[self._index].on

    @property
    def extra_state_attributes(self) -> dict:
        led = self.hub.state.leds[self._index]
        return {"color": led.color, "blinking": led.blinking, "label": led.label}


class TelenotZone(TelenotEntity, BinarySensorEntity):
    """Meldebereich display LED (on = message/open)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, hub: TelenotHub, index: int) -> None:
        super().__init__(hub, f"meldebereich_{index + 1}")
        self._index = index
        mb = hub.state.meldebereiche[index]
        self._attr_name = " ".join(t for t in (mb.label, mb.label2) if t)

    @property
    def is_on(self) -> bool:
        return self.hub.state.meldebereiche[self._index].info

    @property
    def extra_state_attributes(self) -> dict:
        mb = self.hub.state.meldebereiche[self._index]
        return {"gesperrt": mb.gesperrt, "blinking": mb.info_blinking, "index": self._index + 1}
