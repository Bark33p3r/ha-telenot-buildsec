"""Config flow for Telenot BuildSec."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    LEGACY_KEY_SEQUENCES,
    CONF_ADDRESS,
    CONF_ARM_AWAY_KEYS,
    CONF_ARM_HOME_KEYS,
    CONF_AWAY_LED,
    CONF_BEDIENTEILCODE,
    CONF_CODE_REQUIRED,
    CONF_DISARM_KEYS,
    CONF_FREISCHALTCODE,
    CONF_HOME_LED,
    DEFAULT_ARM_AWAY_KEYS,
    DEFAULT_ARM_HOME_KEYS,
    DEFAULT_DISARM_KEYS,
    DOMAIN,
    LED_AUTO,
    LED_CHOICES,
)
from .telenot import BuildSecClient, BuildSecError, S2KeyError, S2RemoteBusy

_LOGGER = logging.getLogger(__name__)

HEX16 = re.compile(r"^[0-9a-fA-F]{16}$")


def _user_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, vol.UNDEFINED)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_FREISCHALTCODE, default=defaults.get(CONF_FREISCHALTCODE, "")): str,
            vol.Required(CONF_BEDIENTEILCODE): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_ADDRESS, default=defaults.get(CONF_ADDRESS, 0)): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=15)
            ),
        }
    )


async def _test_connection(data: dict[str, Any]) -> str | None:
    client = BuildSecClient(
        data[CONF_HOST], data[CONF_PORT], data[CONF_FREISCHALTCODE],
        data[CONF_BEDIENTEILCODE], data[CONF_ADDRESS],
    )
    try:
        await asyncio.wait_for(client.connect(load_labels=False), 45)
    except S2RemoteBusy:
        return "busy"
    except S2KeyError:
        return "invalid_freischaltcode"
    except BuildSecError as err:
        _LOGGER.warning("Panel rejected login: %s", err)
        return "rejected"
    except (OSError, asyncio.TimeoutError):
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error while connecting")
        return "unknown"
    finally:
        await client.disconnect()
    return None



def _keys(opts: dict, conf: str, default: str) -> str:
    value = opts.get(conf, default)
    return LEGACY_KEY_SEQUENCES.get(value.replace(" ", ""), value)

class TelenotConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_FREISCHALTCODE] = user_input[CONF_FREISCHALTCODE].strip()
            code = user_input[CONF_BEDIENTEILCODE].strip()
            user_input[CONF_BEDIENTEILCODE] = code
            if not HEX16.match(user_input[CONF_FREISCHALTCODE]):
                errors[CONF_FREISCHALTCODE] = "invalid_freischaltcode_format"
            elif not (code.isdigit() and 1 <= len(code) <= 6):
                errors[CONF_BEDIENTEILCODE] = "invalid_code_format"
            else:
                await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
                self._abort_if_unique_id_configured()
                error = await _test_connection(user_input)
                if error is None:
                    return self.async_create_entry(
                        title=f"Telenot {user_input[CONF_HOST]}", data=user_input
                    )
                errors["base"] = error
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TelenotOptionsFlow()


class TelenotOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        led_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=LED_CHOICES, translation_key="led_choice",
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        schema = vol.Schema(
            {
                vol.Optional(CONF_ARM_AWAY_KEYS, default=_keys(opts, CONF_ARM_AWAY_KEYS, DEFAULT_ARM_AWAY_KEYS)): str,
                vol.Optional(CONF_ARM_HOME_KEYS, default=_keys(opts, CONF_ARM_HOME_KEYS, DEFAULT_ARM_HOME_KEYS)): str,
                vol.Optional(CONF_DISARM_KEYS, default=_keys(opts, CONF_DISARM_KEYS, DEFAULT_DISARM_KEYS)): str,
                vol.Optional(CONF_AWAY_LED, default=opts.get(CONF_AWAY_LED, LED_AUTO)): led_selector,
                vol.Optional(CONF_HOME_LED, default=opts.get(CONF_HOME_LED, LED_AUTO)): led_selector,
                vol.Optional(CONF_CODE_REQUIRED, default=opts.get(CONF_CODE_REQUIRED, True)): bool,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"code": "{code}", "raw": "{0xNN}"},
        )
