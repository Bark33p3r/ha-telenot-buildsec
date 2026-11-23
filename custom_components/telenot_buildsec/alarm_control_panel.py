"""Alarm control panel for Telenot BuildSec."""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TelenotConfigEntry
from .const import (
    CONF_ARM_AWAY_KEYS,
    CONF_ARM_HOME_KEYS,
    CONF_AWAY_LED,
    CONF_CODE_REQUIRED,
    CONF_DISARM_KEYS,
    CONF_HOME_LED,
    DEFAULT_ARM_AWAY_KEYS,
    DEFAULT_ARM_HOME_KEYS,
    DEFAULT_DISARM_KEYS,
    LED_AUTO,
    LED_NONE,
    LEGACY_KEY_SEQUENCES,
    VERSION,
)
from .entity import TelenotEntity
from .hub import TelenotHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TelenotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TelenotAlarmPanel(entry.runtime_data, dict(entry.options))])


def _led_label(hub: TelenotHub, idx: int) -> str:
    led = hub.state.leds[idx]
    return f"{led.label} {led.label2}".lower()


def _auto_led(hub: TelenotHub, home: bool, ready: bool = False) -> int | None:
    """Find the keypad LED labelled "extern/intern scharf" (or "... bereit" if ready=True)."""
    candidates = []
    for idx in range(len(hub.state.leds)):
        label = _led_label(hub, idx)
        if "scharf" not in label or "unscharf" in label:
            continue
        if ("bereit" in label) != ready:
            continue
        if home and "intern" in label:
            return idx
        if not home and "extern" in label:
            return idx
        candidates.append((idx, label))
    if not home and len(candidates) == 1 and "intern" not in candidates[0][1]:
        return candidates[0][0]
    return None


class TelenotAlarmPanel(TelenotEntity, AlarmControlPanelEntity):
    _attr_name = None
    _attr_translation_key = "alarm"

    def __init__(self, hub: TelenotHub, options: dict) -> None:
        super().__init__(hub, "alarm")
        def keys(conf: str, default: str) -> str:
            value = options.get(conf, default)
            return LEGACY_KEY_SEQUENCES.get(value.replace(" ", ""), value)

        self._arm_away_keys = keys(CONF_ARM_AWAY_KEYS, DEFAULT_ARM_AWAY_KEYS)
        self._arm_home_keys = keys(CONF_ARM_HOME_KEYS, DEFAULT_ARM_HOME_KEYS)
        self._disarm_keys = keys(CONF_DISARM_KEYS, DEFAULT_DISARM_KEYS)
        self._away_led = options.get(CONF_AWAY_LED, LED_AUTO)
        self._home_led = options.get(CONF_HOME_LED, LED_AUTO)
        self._code_required = options.get(CONF_CODE_REQUIRED, True)
        features = AlarmControlPanelEntityFeature(0)
        if self._arm_away_keys:
            features |= AlarmControlPanelEntityFeature.ARM_AWAY
        if self._arm_home_keys:
            features |= AlarmControlPanelEntityFeature.ARM_HOME
        self._attr_supported_features = features
        self._attr_code_format = CodeFormat.NUMBER if self._code_required else None
        self._attr_code_arm_required = self._code_required

    def _led_index(self, setting: str, home: bool) -> int | None:
        if setting == LED_NONE:
            return None
        idx = None if setting == LED_AUTO else int(setting) - 1
        # A "... bereit" LED only says the panel *could* be armed - never use it as armed state.
        if idx is None or "bereit" in _led_label(self.hub, idx):
            return _auto_led(self.hub, home)
        return idx

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        st = self.hub.state
        if st.alarm:
            return AlarmControlPanelState.TRIGGERED
        led_found = False
        for setting, home, armed in (
            (self._away_led, False, AlarmControlPanelState.ARMED_AWAY),
            (self._home_led, True, AlarmControlPanelState.ARMED_HOME),
        ):
            idx = self._led_index(setting, home)
            if idx is not None:
                led_found = True
                led = st.leds[idx]
                if led.on:
                    return AlarmControlPanelState.ARMING if led.blinking else armed
        if led_found:
            return AlarmControlPanelState.DISARMED
        # Fallback without LEDs: keypad display text.
        text = f"{st.line1} {st.line2}".lower().replace("scharf bereit", "")
        if "unscharf" in text:
            return AlarmControlPanelState.DISARMED
        if "intern scharf" in text:
            return AlarmControlPanelState.ARMED_HOME
        if "scharf" in text:
            return AlarmControlPanelState.ARMED_AWAY
        return AlarmControlPanelState.DISARMED

    @property
    def extra_state_attributes(self) -> dict:
        st = self.hub.state
        ready = {}
        for key, home in (("bereit_extern", False), ("bereit_intern", True)):
            idx = _auto_led(self.hub, home, ready=True)
            if idx is not None:
                ready[key] = st.leds[idx].on
        return {
            **ready,
            "version": VERSION,
            "display": f"{st.line1}\n{st.line2}".strip(),
            "away_led": self._led_index(self._away_led, False),
            "home_led": self._led_index(self._home_led, True),
            "last_error": self.hub.last_error or st.last_error,
        }

    async def _run(self, template: str, code: str | None) -> None:
        if not template:
            raise ServiceValidationError("Für diese Aktion ist keine Tastenfolge konfiguriert")
        if "{code}" in template:
            if self._code_required and not code:
                raise ServiceValidationError("Bitte Code eingeben")
            code = code or self.hub.bedienteilcode
            if not code.isdigit():
                raise ServiceValidationError("Der Code darf nur Ziffern enthalten")
        if not self.hub.available:
            raise HomeAssistantError("Keine Verbindung zur Alarmanlage")
        await self.hub.async_press(template.replace("{code}", code or ""))

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._run(self._disarm_keys, code)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._run(self._arm_away_keys, code)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._run(self._arm_home_keys, code)
