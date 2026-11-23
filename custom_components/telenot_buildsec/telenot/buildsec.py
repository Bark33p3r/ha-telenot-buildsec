"""BuildSec application layer: a virtual keypad (Bedienteil) for complex 200H/400H.

Over the S2 session the app sends VdS 2465 "Satztyp 70" (transparent data
block) records with Telenot specific Kennungen. After the remote service is
switched on, the panel tunnels the Com2Bus keypad telegrams (display text,
LEDs, buzzer, labels) to us, and every key press is sent as a single byte.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from .s2 import KeyConfig, S2Error, S2Session

_LOGGER = logging.getLogger(__name__)

SATZTYP_DATENBLOCK_TRANSPARENT = 0x70

# Satztyp 70 Kennungen (EDatenblockKennungTelenot)
K_GTYP_ANFORDERN = 0x18
K_GTYP_DATEN = 0x19
K_FEHLER = 0x23
K_FERN_VERB_ABGEB_OK = 0x4B
K_FERNSERVICE_BETREIBER_LEGACY = 0x8A
K_FERNSERVICE_BETREIBER_LEGACY_OK = 0x8B
K_FERNSERVICE_ERRICHTER_LEGACY_OK = 0x8D
K_FERNSERVICE_BETREIBER = 0x92
K_FERNSERVICE_BETREIBER_OK = 0x93
K_FERNSERVICE_ERRICHTER_OK = 0x95
K_COM2BUS_ANFRAGE = 0x98
K_COM2BUS_ANTWORT = 0x99
K_FERNBEDIENUNG = 0x9E
K_FERNBEDIENUNG_OK = 0x9F

LOGIN_OK = {
    K_FERNSERVICE_BETREIBER_OK,
    K_FERNSERVICE_ERRICHTER_OK,
    K_FERNBEDIENUNG_OK,
    K_FERNSERVICE_BETREIBER_LEGACY_OK,
    K_FERNSERVICE_ERRICHTER_LEGACY_OK,
}

# Com2Bus Kennungen
C_TEXT_LCD = 0x54
C_LEDS_EIN_AUS = 0xC0
C_LEDS_BLINKEN = 0xC1
C_SCHALTFUNKTION_ZUSTAND = 0xCA
C_BESCHRIFTUNG = 0xCD
C_TASTENCODE_ACK = 0xCE
C_DISPLAY_TASTATUR = 0xCF
C_TEXT_ABFRAGE = 0xEA

# Error reasons (EFehlergrund)
FEHLERGRUND = {
    0x10: "Kennwort (Bedienteilcode) falsch",
    0x11: "Checksumme falsch",
    0x13: "Falsche Satzlänge",
    0x14: "Unbekannte Kennung",
    0x1B: "Fernservice gesperrt",
    0x1D: "Unzulässige Bedienteiladresse",
    0x1E: "Com2Bus für EMZ nicht aktiviert",
    0x1F: "Com2Bus-Störung zur EMZ",
    0x20: "Code gesperrt",
    0x21: "Fehler Funktion",
    0x22: "Bedienebene zu klein",
    0x23: "Bedienteil belegt – andere Adresse verwenden",
    0x24: "Autorisierung fehlgeschlagen",
    0x25: "Eingabe gesperrt",
    0x26: "LAN-Verbindung nicht erlaubt",
    0x27: "WAN-Verbindung nicht erlaubt",
    0x28: "hiXserver-Verbindung nicht erlaubt",
    0x29: "IP-Adresse nicht berechtigt",
    0x30: "Gesperrt durch Zeitmodell",
}
FEHLER_UNBEKANNTE_KENNUNG = 0x14
FEHLER_FERNSERVICE_GESPERRT = 0x1B

# Keypad keys as sent by the app (ASCII codes). Meaning from the app's key images:
# A = Unscharf, B = OK, E = Extern scharf, I = Intern scharf, S = Esc, + = runter, - = hoch.
# 0x4E (Kreis-Taste, in der App nur Langdruck) ist der Notruf und wird absichtlich nie gesendet.
KEYS = {
    **{str(d): 0x30 + d for d in range(10)},
    "A": 0x41,
    "B": 0x42,
    "E": 0x45,
    "I": 0x49,
    "S": 0x53,
    "+": 0x2B,
    "-": 0x2D,
}
KEY_NOTRUF = 0x4E
KEY_SCHALTFUNKTION_BASE = 0xD0  # + index 0..15
KEY_MELDEBEREICH_SPERREN_BASE = 0x80  # + index

LED_COLORS = {0: "off", 1: "green", 2: "red", 3: "yellow"}

# HD44780 style umlauts used by the keypad display.
_UMLAUTE = {0xE1: "ä", 0xF5: "ü", 0xEF: "ö"}


class BuildSecError(Exception):
    """Error reported by the panel on application level."""

    def __init__(self, grund: int) -> None:
        self.grund = grund
        super().__init__(FEHLERGRUND.get(grund, f"Fehler 0x{grund:02X}"))


def encode_kennwort(code: str) -> bytes:
    """Bedienteilcode (max. 6 digits) -> 3 bytes, nibble-swapped BCD, F-padded."""
    if not code or len(code) > 6 or not code.isdigit():
        raise ValueError("Der Bedienteilcode muss aus 1–6 Ziffern bestehen")
    digits = [int(c) for c in code]
    out = bytearray(b"\xff" * 3)
    pairs = len(digits) // 2
    for i in range(pairs):
        out[i] = (digits[2 * i + 1] << 4) | digits[2 * i]
    if len(digits) % 2:
        out[pairs] = 0xF0 | digits[-1]
    return bytes(out)


def _decode_text(raw: bytes) -> str:
    if raw == b"\xff" * len(raw) and raw:
        return ""  # display test pattern
    chars = []
    for b in raw:
        if b in _UMLAUTE:
            chars.append(_UMLAUTE[b])
        elif b == 0xE8:
            chars.append("v")
        else:
            chars.append(bytes([b]).decode("latin-1"))
    return "".join(chars).strip()


def st70(geraet_frei: int, datenblock: bytes) -> bytes:
    """Build a VdS 2465 Satztyp 70 Nutzerdatensatz."""
    return bytes([len(datenblock) + 1, SATZTYP_DATENBLOCK_TRANSPARENT, geraet_frei]) + datenblock


def iter_saetze(data: bytes):
    """Iterate VdS 2465 Nutzerdatensätze: [len, satztyp, len bytes...]."""
    pos = 0
    while pos + 2 <= len(data):
        length = data[pos]
        end = pos + 2 + length
        if end > len(data):
            _LOGGER.debug("Truncated Nutzerdatensatz in %s", data.hex(" "))
            return
        yield data[pos:end]
        pos = end


@dataclass
class Led:
    color: str = "off"
    blinking: bool = False
    label: str = ""
    label2: str = ""

    @property
    def on(self) -> bool:
        return self.color != "off"


@dataclass
class Meldebereich:
    info: bool = False
    info_blinking: bool = False
    gesperrt: bool = False
    label: str = ""
    label2: str = ""


@dataclass
class Schaltfunktion:
    active: bool = False
    label: str = ""
    text_active: str = ""
    text_passive: str = ""


@dataclass
class KeypadState:
    connected: bool = False
    line1: str = ""
    line2: str = ""
    betrieb: bool = False
    alarm: bool = False
    stoerung: bool = False
    gehtest: bool = False
    betrieb_blinking: bool = False
    alarm_blinking: bool = False
    stoerung_blinking: bool = False
    gehtest_blinking: bool = False
    buzzer: str = "off"
    leds: list[Led] = field(default_factory=lambda: [Led() for _ in range(4)])
    meldebereiche: list[Meldebereich] = field(
        default_factory=lambda: [Meldebereich() for _ in range(32)]
    )
    schaltfunktionen: list[Schaltfunktion] = field(
        default_factory=lambda: [Schaltfunktion() for _ in range(16)]
    )
    labels_loaded: bool = False
    firmware: str | None = None
    legacy_mode: bool = False
    last_error: str | None = None


def _buzzer(b: int) -> str:
    if b == 0:
        return "off"
    if b & 0x10:
        return "continuous"
    if b & 0x08:
        return "beep_3s_pause"
    if b & 0x04:
        return "intermittent"
    if b & 0x02:
        return "3s"
    if b & 0x01:
        return "1s"
    return "off"


class BuildSecClient:
    """Virtual keypad connection to one alarm panel."""

    def __init__(
        self,
        host: str,
        port: int,
        freischaltcode: str,
        bedienteilcode: str,
        bedienteil_adresse: int = 0,
        on_update: Callable[[KeypadState], None] | None = None,
        on_disconnect: Callable[[Exception | None], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self._key_config = KeyConfig.betreiber(freischaltcode)
        self._kennwort = encode_kennwort(bedienteilcode)
        self._address = bedienteil_adresse
        self._on_update = on_update
        self._on_disconnect = on_disconnect
        self.state = KeypadState()
        self._session: S2Session | None = None
        self._waiters: dict[str, asyncio.Future] = {}
        self._key_lock = asyncio.Lock()
        self.raw_listener: Callable[[str, bytes], None] | None = None

    # ------------------------------------------------------------------ API

    @property
    def connected(self) -> bool:
        return bool(self._session and self._session.established and self.state.connected)

    async def connect(self, load_labels: bool = True) -> None:
        self.state = KeypadState()
        self._session = S2Session(
            self.host, self.port, self._key_config, self._handle_nutzdaten, self._session_closed
        )
        await self._session.open()
        try:
            await self._session.set_polltime(2000)
            await self._login()
            self.state.connected = True
            await self._read_device_type()
            if load_labels:
                await self.load_labels()
        except BaseException:
            await self._session.close()
            raise
        self._notify()

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()
        self.state.connected = False
        self._notify()

    async def press(self, key: str | int, wait_ack: bool = True) -> None:
        """Press a single key ("0"-"9", "A", "B", "E", "I", "S", "+", "-" or a raw code)."""
        code = key if isinstance(key, int) else KEYS[key.upper()]
        if code == KEY_NOTRUF:
            raise ValueError("Die Notruf-Taste wird nicht gesendet")
        if not self.connected:
            raise S2Error("nicht verbunden")
        async with self._key_lock:
            fut = self._new_waiter("ack") if wait_ack else None
            await self._session.send_nutzdaten(bytes([code]))
            if fut:
                try:
                    await asyncio.wait_for(fut, 10)
                except asyncio.TimeoutError:
                    _LOGGER.debug("No key acknowledge for 0x%02X", code)
                finally:
                    self._waiters.pop("ack", None)

    @staticmethod
    def parse_keys(keys: str) -> list[int]:
        """Translate "1234E" (``{0xNN}`` = raw code) into key codes; raises ValueError."""
        codes: list[int] = []
        i = 0
        while i < len(keys):
            ch = keys[i]
            if ch.isspace():
                i += 1
                continue
            if ch == "{":
                end = keys.find("}", i)
                if end < 0:
                    raise ValueError("'{' ohne '}' in der Tastenfolge")
                code = int(keys[i + 1 : end], 0)
                i = end + 1
            else:
                if ch.upper() not in KEYS:
                    raise ValueError(f"Unbekannte Taste '{ch}'")
                code = KEYS[ch.upper()]
                i += 1
            if code == KEY_NOTRUF:
                raise ValueError("Die Notruf-Taste wird nicht gesendet")
            if not 0 <= code <= 0xFF:
                raise ValueError(f"Ungültiger Tastencode {code}")
            codes.append(code)
        return codes

    async def press_sequence(self, keys: str, delay: float = 0.3) -> None:
        """Press several keys, e.g. "1234E". Whitespace is ignored.

        ``{0xNN}`` inserts a raw key code. The whole sequence is validated first.
        """
        for code in self.parse_keys(keys):
            await self.press(code)
            if delay:
                await asyncio.sleep(delay)

    async def toggle_schaltfunktion(self, index: int) -> None:
        """Execute switch function key 1..16 (needs to be configured in the panel)."""
        await self.press(KEY_SCHALTFUNKTION_BASE + index - 1)

    async def load_labels(self, timeout: float = 60.0) -> None:
        """Request LED / zone / switch function labels (like the app does on connect)."""
        fut = self._new_waiter("texte")
        datenblock = bytes([K_COM2BUS_ANFRAGE]) + self._kennwort + bytes(
            [self._address, 0x00, C_TEXT_ABFRAGE]
        )
        await self._session.send_nutzdaten(st70(0x10, datenblock))
        try:
            await asyncio.wait_for(fut, timeout)
            self.state.labels_loaded = True
        except asyncio.TimeoutError:
            _LOGGER.warning("Beschriftungstexte wurden nicht vollständig empfangen")
        finally:
            self._waiters.pop("texte", None)

    # ------------------------------------------------------------ internals

    def _new_waiter(self, name: str) -> asyncio.Future:
        fut = asyncio.get_running_loop().create_future()
        self._waiters[name] = fut
        return fut

    def _resolve(self, name: str, value=None, error: Exception | None = None) -> bool:
        fut = self._waiters.get(name)
        if fut and not fut.done():
            if error:
                fut.set_exception(error)
            else:
                fut.set_result(value)
            return True
        return False

    async def _login(self) -> None:
        fut = self._new_waiter("login")
        datenblock = bytes([K_FERNSERVICE_BETREIBER]) + self._kennwort + bytes([self._address])
        await self._session.send_nutzdaten(st70(0x10, datenblock))
        try:
            await asyncio.wait_for(fut, 20)
        except BuildSecError as err:
            if err.grund not in (FEHLER_UNBEKANNTE_KENNUNG, FEHLER_FERNSERVICE_GESPERRT):
                raise
            _LOGGER.info("Fernservice not accepted (%s), trying legacy mode", err)
            self.state.legacy_mode = True
            fut = self._new_waiter("login")
            datenblock = bytes([K_FERNSERVICE_BETREIBER_LEGACY]) + self._kennwort + bytes(
                [self._address]
            )
            await self._session.send_nutzdaten(st70(0x10, datenblock))
            await asyncio.wait_for(fut, 20)
        finally:
            self._waiters.pop("login", None)

    async def _read_device_type(self) -> None:
        fut = self._new_waiter("gtyp")
        await self._session.send_nutzdaten(st70(0x00, bytes([K_GTYP_ANFORDERN]) + self._kennwort))
        try:
            data = await asyncio.wait_for(fut, 10)
            if len(data) >= 5:
                self.state.firmware = f"{data[2]}.{data[3]}.{data[4]}"
        except (asyncio.TimeoutError, BuildSecError) as err:
            _LOGGER.debug("GTyp request failed: %s", err)
        finally:
            self._waiters.pop("gtyp", None)

    def _session_closed(self, error: Exception | None) -> None:
        was_connected = self.state.connected
        self.state.connected = False
        for name in list(self._waiters):
            self._resolve(name, error=error or S2Error("Verbindung beendet"))
        if was_connected:
            self._notify()
            if self._on_disconnect:
                self._on_disconnect(error)

    def _notify(self) -> None:
        if self._on_update:
            self._on_update(self.state)

    def _handle_nutzdaten(self, data: bytes) -> None:
        if self.raw_listener:
            self.raw_listener("rx", data)
        if len(data) == 1:
            return
        changed = False
        for satz in iter_saetze(data):
            if satz[1] == SATZTYP_DATENBLOCK_TRANSPARENT and len(satz) >= 4:
                changed |= self._handle_st70(satz[3:])
            else:
                # Legacy mode: Com2Bus records are sent without Satztyp 70 wrapper.
                changed |= self._handle_com2bus(satz[1:])
        if changed:
            self._notify()

    def _handle_st70(self, datenblock: bytes) -> bool:
        kennung = datenblock[0]
        if kennung in LOGIN_OK:
            self._resolve("login", datenblock)
            return False
        if kennung == K_GTYP_DATEN:
            self._resolve("gtyp", datenblock)
            return False
        if kennung == K_FEHLER and len(datenblock) >= 2:
            err = BuildSecError(datenblock[1])
            self.state.last_error = str(err)
            _LOGGER.warning("Alarmanlage meldet: %s", err)
            for name in ("login", "gtyp", "texte"):
                if self._resolve(name, error=err):
                    break
            return True
        if kennung == K_FERN_VERB_ABGEB_OK:
            return False
        if kennung == K_COM2BUS_ANTWORT:
            changed = False
            for satz in iter_saetze(datenblock[2:]):
                changed |= self._handle_com2bus(satz[1:])
            return changed
        _LOGGER.debug("Unknown Satztyp 70 Kennung 0x%02X: %s", kennung, datenblock.hex(" "))
        return False

    def _handle_com2bus(self, block: bytes) -> bool:
        if not block:
            return False
        kennung = block[0]
        st = self.state
        if kennung == C_TEXT_LCD:
            changed = False
            for marker, attr in ((0x80, "line1"), (0xC0, "line2")):
                idx = _find_esc(block, marker)
                if idx is not None:
                    setattr(st, attr, _decode_text(block[idx : idx + 16]))
                    changed = True
            return changed
        if kennung == C_LEDS_EIN_AUS:
            if len(block) >= 2:
                st.buzzer = _buzzer(block[1])
            if len(block) >= 3:
                b = block[2]
                st.betrieb, st.alarm = bool(b & 1), bool(b & 2)
                st.stoerung, st.gehtest = bool(b & 4), bool(b & 8)
                st.leds[0].color = LED_COLORS[(b >> 4) & 3]
                st.leds[1].color = LED_COLORS[(b >> 6) & 3]
            if len(block) >= 4:
                st.leds[2].color = LED_COLORS[block[3] & 3]
                st.leds[3].color = LED_COLORS[(block[3] >> 2) & 3]
            _apply_bits(block, 4, st.meldebereiche, 0, "info")
            _apply_bits(block, 5, st.meldebereiche, 8, "info")
            _apply_bits(block, 6, st.meldebereiche, 0, "gesperrt")
            _apply_bits(block, 7, st.meldebereiche, 8, "gesperrt")
            _apply_bits(block, 8, st.meldebereiche, 16, "info")
            _apply_bits(block, 9, st.meldebereiche, 24, "info")
            _apply_bits(block, 10, st.meldebereiche, 16, "gesperrt")
            _apply_bits(block, 11, st.meldebereiche, 24, "gesperrt")
            return True
        if kennung == C_LEDS_BLINKEN:
            if len(block) >= 3:
                b = block[2]
                st.betrieb_blinking, st.alarm_blinking = bool(b & 1), bool(b & 2)
                st.stoerung_blinking, st.gehtest_blinking = bool(b & 4), bool(b & 8)
                for led, bits in ((st.leds[0], (b >> 4) & 3), (st.leds[1], (b >> 6) & 3)):
                    led.blinking = bits != 0
                    if bits and not led.on:
                        led.color = LED_COLORS[bits]
            if len(block) >= 4:
                for led, bits in ((st.leds[2], block[3] & 3), (st.leds[3], (block[3] >> 2) & 3)):
                    led.blinking = bits != 0
                    if bits and not led.on:
                        led.color = LED_COLORS[bits]
            _apply_bits(block, 4, st.meldebereiche, 0, "info_blinking")
            _apply_bits(block, 5, st.meldebereiche, 8, "info_blinking")
            return True
        if kennung == C_SCHALTFUNKTION_ZUSTAND and len(block) >= 3:
            _apply_bits(block, 1, st.schaltfunktionen, 0, "active")
            _apply_bits(block, 2, st.schaltfunktionen, 8, "active")
            return True
        if kennung == C_BESCHRIFTUNG:
            return self._handle_label(block)
        if kennung == C_TASTENCODE_ACK:
            self._resolve("ack", block[1] if len(block) > 1 else None)
            return False
        if kennung == C_DISPLAY_TASTATUR:
            return False
        _LOGGER.debug("Unknown Com2Bus block: %s", block.hex(" "))
        return False

    def _handle_label(self, block: bytes) -> bool:
        if len(block) < 2:
            return False
        if block[1] == 2:  # last transmission (carries CRC)
            self._resolve("texte", True)
            return True
        if len(block) < 5:
            return False
        index, n1, n2 = block[2], block[3], block[4]
        text1 = _decode_text(block[5 : 5 + n1])
        text2 = _decode_text(block[5 + n1 : 5 + n1 + n2])
        st = self.state
        if index <= 3:
            st.leds[index].label, st.leds[index].label2 = text1, text2
        elif index <= 35:
            mb = st.meldebereiche[index - 4]
            mb.label, mb.label2 = text1, text2
        elif index <= 83:
            sf = st.schaltfunktionen[(index - 36) // 3]
            kind = (index - 36) % 3
            text = " ".join(t for t in (text1, text2) if t)
            if kind == 0:
                sf.label = text
            elif kind == 1:
                sf.text_active = text
            else:
                sf.text_passive = text
        return False


def _find_esc(block: bytes, marker: int) -> int | None:
    for i in range(len(block) - 1):
        if block[i] == 0x1B and block[i + 1] == marker:
            return i + 2
    return None


def _apply_bits(block: bytes, pos: int, items: list, offset: int, attr: str) -> None:
    if len(block) <= pos:
        return
    for bit in range(8):
        setattr(items[offset + bit], attr, bool(block[pos] & (1 << bit)))
