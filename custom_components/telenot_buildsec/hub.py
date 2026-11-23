"""Connection manager: keeps the keypad session alive and reconnects."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import signal_update
from .telenot import BuildSecClient, BuildSecError, KeypadState, S2KeyError, S2RemoteBusy

_LOGGER = logging.getLogger(__name__)

RETRY_DELAYS = (10, 30, 60, 120, 300)


class TelenotHub:
    """Owns the BuildSec client for one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        port: int,
        freischaltcode: str,
        bedienteilcode: str,
        address: int,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.bedienteilcode = bedienteilcode
        self.client = BuildSecClient(
            host, port, freischaltcode, bedienteilcode, address,
            on_update=self._on_update,
            on_disconnect=self._on_disconnect,
        )
        self.enabled = True
        self._task: asyncio.Task | None = None
        self._disconnected = asyncio.Event()
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def state(self) -> KeypadState:
        return self.client.state

    @property
    def available(self) -> bool:
        return self.client.connected

    async def async_first_connect(self) -> None:
        """Initial connection during setup (raises on failure)."""
        await self.client.connect(load_labels=True)
        self.last_error = None

    def start(self, entry) -> None:
        self._task = entry.async_create_background_task(
            self.hass, self._run(), f"telenot_buildsec_{self.entry_id}"
        )

    async def async_stop(self) -> None:
        self.enabled = False
        self._wake.set()
        if self._task:
            self._task.cancel()
        await self.client.disconnect()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Connection switch: off releases the panel for the BuildSec app."""
        self.enabled = enabled
        if not enabled:
            await self.client.disconnect()
        self._wake.set()
        self._notify()

    async def async_press(self, keys: str) -> None:
        try:
            self.client.parse_keys(keys)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        async with self._lock:
            await self.client.press_sequence(keys)

    async def _run(self) -> None:
        attempt = 0
        while True:
            if self.enabled and not self.client.connected:
                try:
                    self._disconnected.clear()
                    await self.client.connect(load_labels=not self.state.labels_loaded)
                    _LOGGER.info("Verbunden mit Telenot-Anlage %s", self.client.host)
                    self.last_error = None
                    attempt = 0
                except S2RemoteBusy as err:
                    self._set_error(str(err), logging.INFO)
                    attempt = max(attempt, 2)
                except (S2KeyError, BuildSecError) as err:
                    self._set_error(str(err), logging.ERROR)
                    attempt = len(RETRY_DELAYS) - 1
                except (OSError, asyncio.TimeoutError, Exception) as err:  # noqa: BLE001
                    self._set_error(f"{type(err).__name__}: {err}", logging.WARNING)
                    attempt += 1
            if self.enabled and self.client.connected:
                await self._disconnected.wait()
                continue
            self._wake.clear()
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)] if self.enabled else None
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except asyncio.TimeoutError:
                pass

    def _set_error(self, message: str, level: int) -> None:
        if message != self.last_error:
            _LOGGER.log(level, "Telenot %s: %s", self.client.host, message)
        self.last_error = message
        self._notify()

    @callback
    def _on_update(self, _state: KeypadState) -> None:
        self._notify()

    @callback
    def _on_disconnect(self, error: Exception | None) -> None:
        if error:
            self.last_error = str(error)
            _LOGGER.warning("Verbindung zur Telenot-Anlage getrennt: %s", error)
        self._disconnected.set()
        self._notify()

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, signal_update(self.entry_id))
