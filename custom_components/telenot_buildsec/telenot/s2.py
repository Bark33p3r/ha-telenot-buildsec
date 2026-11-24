"""VdS 2465 S2 transport (TCP) with the Telenot protocol extension (PK 0x82).

This is the transport the BuildSec app uses to talk to a complex 200H/400H
over LAN. The alarm panel is the protocol *master*: it starts the session with
a SYNC_REQUEST and polls; we are the *slave* and answer every telegram we
receive with exactly one telegram (POLL or DATA).

Frame on the wire::

    K (2, BE)  key number, 0 = plain
    SL (2, BE) length of the (encrypted) telegram
    telegram   SL bytes, AES-128-CBC (IV = 0, no padding) unless K == 0

Plain telegram::

    TC(4) CRC(2) RC(4) IK(1) PK(1) L(1)  [Telenot: NL(2) V(1) NK(1)]  Nutzdaten

All multi-byte values are big endian. CRC is the internet checksum over the
telegram with the CRC field set to zero.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

PK_VDS = 0x01
PK_TELENOT = 0x82

IK_SYNC_REQUEST = 1
IK_SYNC_RESPONSE = 2
IK_POLLING = 3
IK_DATA = 4
IK_ERROR_UNKNOWN_IK = 5
IK_ERROR_UNKNOWN_PK = 6
IK_SERVICE_REQUEST = 7

NK_USERDATA_VDS = 1
NK_POLLTIME_SET = 5
NK_POLLTIME_ACK = 6
NK_SESSION_CLOSE = 9
NK_ERROR_SESSION_CANCELED = 0xFC
NK_ERROR_VERSION_MISMATCH = 0xFD
NK_ERROR_INVALID_ID = 0xFE

KEY_PLAIN = 0
KEY_UNKNOWN = 0xFFFF
# Key number BuildSec uses for "Betreiber" (operator) access with a Freischaltcode.
KEY_BUILDSEC_BETREIBER = 9997

PROTOCOL_VERSION = 3
MIN_PADDED_LENGTH = 256

# Function monitoring time of the app (24 s) plus a little margin.
RECEIVE_TIMEOUT = 25.0
CONNECT_TIMEOUT = 15.0


class S2Error(Exception):
    """Generic S2 protocol error."""


class S2RemoteBusy(S2Error):
    """The panel already has a remote session (another app is connected)."""


class S2KeyError(S2Error):
    """The panel does not accept our key / Freischaltcode."""


def internet_checksum(buf: bytes) -> int:
    """RFC 1071 style checksum exactly as Telenot implements it."""
    total = 0
    length = len(buf)
    i = 0
    while length > 1:
        total += (buf[i] << 8) | buf[i + 1]
        if total & 0xFFFF0000:
            total = (total & 0xFFFF) + 1
        i += 2
        length -= 2
    if length > 0:
        total += buf[i] << 8
        if total & 0xFFFF0000:
            total = (total & 0xFFFF) + 1
    return (~total) & 0xFFFF


def derive_betreiber_key(freischaltcode: bytes, tc: int) -> bytes:
    """Session key for key number 9997, derived from the Freischaltcode.

    ``tc`` is the transmission counter of the panel's SYNC_REQUEST.
    """
    if len(freischaltcode) < 8:
        raise ValueError("Freischaltcode must be 8 bytes (16 hex characters)")
    t = tc.to_bytes(4, "big")
    k = bytearray(16)
    k[0:8] = freischaltcode[0:8]
    k[8] = 0x74 ^ t[3]
    k[9] = 0x19 ^ t[2]
    k[10] = 0x10 ^ t[1]
    k[11] = 0x18 ^ t[0]
    k[12] = 0x10 ^ t[3]
    k[13] = 0x07 ^ t[2]
    k[14] = 0x19 ^ t[1]
    k[15] = 0x56 ^ t[0]
    for i in range(4):
        k[i] ^= t[i]
    for i in range(4):
        k[i + 4] ^= k[i]
    for i in range(4):
        k[i + 8] ^= k[i + 4]
    for i in range(4):
        k[i + 12] ^= k[i + 8]
    return bytes(k)


@dataclass
class KeyConfig:
    """How to encrypt the session."""

    key_number: int
    key: bytes = b""

    @classmethod
    def betreiber(cls, freischaltcode_hex: str) -> KeyConfig:
        """Operator access with the 16 hex digit Freischaltcode."""
        return cls(KEY_BUILDSEC_BETREIBER, bytes.fromhex(freischaltcode_hex.strip()))

    def session_key(self, sync_tc: int) -> bytes | None:
        if self.key_number == KEY_PLAIN:
            return None
        if self.key_number == KEY_BUILDSEC_BETREIBER:
            return derive_betreiber_key(self.key, sync_tc)
        if len(self.key) != 16:
            raise ValueError("AES key must be 16 bytes")
        return self.key


@dataclass
class Telegram:
    tc: int
    rc: int
    ik: int
    pk: int = PK_TELENOT
    nk: int = NK_USERDATA_VDS
    v: int = PROTOCOL_VERSION
    data: bytes = b""

    def serialize(self) -> bytes:
        if self.pk == PK_TELENOT:
            head = struct.pack(
                ">IHIBBBHBB",
                self.tc, 0, self.rc, self.ik, self.pk, 4, len(self.data), self.v, self.nk,
            )
        else:
            head = struct.pack(">IHIBBB", self.tc, 0, self.rc, self.ik, self.pk, len(self.data))
        return head + self.data

    @classmethod
    def parse(cls, stream: bytes) -> Telegram:
        if len(stream) < 13:
            raise S2Error("telegram too short")
        tc, _crc, rc, ik, pk, length = struct.unpack(">IHIBBB", stream[:13])
        if pk == PK_TELENOT:
            if length < 4 or len(stream) < 13 + length:
                raise S2Error("Telenot frame too short")
            nl, v, nk = struct.unpack(">HBB", stream[13:17])
            data = stream[13 + length : 13 + length + nl]
            if len(data) != nl:
                raise S2Error("Nutzdaten length mismatch")
            return cls(tc, rc, ik, pk, nk, v, bytes(data))
        if pk == PK_VDS:
            data = stream[13 : 13 + length]
            if len(data) != length:
                raise S2Error("Nutzdaten length mismatch")
            return cls(tc, rc, ik, pk, NK_USERDATA_VDS, 0, bytes(data))
        raise S2Error(f"unknown PK 0x{pk:02X}")

    def __str__(self) -> str:
        # Header and length only: outgoing payloads contain the keypad code
        # (login block, label request, key presses) and incoming key
        # acknowledges echo every pressed key, so payloads are never logged here.
        # Received Nutzdaten are logged redacted in buildsec.py.
        return (
            f"IK={self.ik} PK=0x{self.pk:02X} NK={self.nk} V={self.v} "
            f"TC={self.tc} RC={self.rc} len={len(self.data)}"
        )


def _aes(key: bytes, data: bytes, encrypt: bool) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(bytes(16)))
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def encode_frame(telegram: Telegram, key_number: int, key: bytes | None) -> bytes:
    stream = bytearray(telegram.serialize())
    if key_number != KEY_PLAIN:
        rest = (16 - len(stream) % 16) % 16
        pad = max(MIN_PADDED_LENGTH - len(stream), rest)
        stream += os.urandom(pad)
    # The app computes the checksum over the padded stream before encrypting.
    struct.pack_into(">H", stream, 4, internet_checksum(bytes(stream)))
    payload = _aes(key, bytes(stream), True) if key_number != KEY_PLAIN else bytes(stream)
    return struct.pack(">HH", key_number, len(payload)) + payload


def decode_payload(key_number: int, payload: bytes, key: bytes | None) -> Telegram | None:
    """Decrypt and parse; returns None if the checksum is wrong (telegram ignored)."""
    if key_number == KEY_UNKNOWN:
        raise S2KeyError("Die Anlage kennt die Schlüsselnummer nicht (Freischaltcode falsch?)")
    if key_number == KEY_PLAIN:
        stream = payload
        full = payload
    else:
        if key is None:
            raise S2KeyError("encrypted telegram before session key is known")
        if len(payload) % 16:
            raise S2Error("encrypted telegram length is not a multiple of 16")
        full = _aes(key, payload, False)
        pk = full[11]
        if pk == PK_TELENOT:
            length = 17 + struct.unpack(">H", full[13:15])[0]
        elif pk == PK_VDS:
            length = 13 + full[12]
        else:
            # Most likely a wrong key: garbage after decryption.
            raise S2KeyError("Entschlüsselung fehlgeschlagen (Freischaltcode falsch?)")
        stream = full[:length]

    def _crc_ok(buf: bytes) -> bool:
        tmp = bytearray(buf)
        stored = struct.unpack(">H", tmp[4:6])[0]
        tmp[4:6] = b"\0\0"
        return internet_checksum(bytes(tmp)) == stored

    if not (_crc_ok(stream) or _crc_ok(full)):
        _LOGGER.debug("Ignoring telegram with wrong checksum")
        return None
    return Telegram.parse(stream)


@dataclass
class _TxItem:
    data: bytes
    future: asyncio.Future[None] = field(repr=False)


class S2Session:
    """One S2 session to the panel. Not reusable after close()."""

    def __init__(
        self,
        host: str,
        port: int,
        key_config: KeyConfig,
        on_nutzdaten: Callable[[bytes], None],
        on_closed: Callable[[Exception | None], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._key_config = key_config
        self._on_nutzdaten = on_nutzdaten
        self._on_closed = on_closed
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._key: bytes | None = None
        self._key_number = KEY_PLAIN
        self._version = PROTOCOL_VERSION
        self._tc = 0
        self._rc = 0
        self._last_sent: Telegram | None = None
        self._tx_queue: deque[_TxItem] = deque()
        self._tx_event = asyncio.Event()
        self._polltime_pending: int | None = None
        self._polltime_sent = False
        self._polltime_future: asyncio.Future[None] | None = None
        self._closing = False
        self._task: asyncio.Task[None] | None = None
        self._read_task: asyncio.Task[tuple[int, bytes]] | None = None
        self.established = False

    # ------------------------------------------------------------------ API

    async def open(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), CONNECT_TIMEOUT
        )
        try:
            self._tc = int.from_bytes(os.urandom(4), "big") & 0x7FFFFFFF
            await self._synchronize()
        except BaseException:
            self._close_socket()
            raise
        self.established = True
        self._task = asyncio.create_task(self._run(), name="telenot-s2")

    async def set_polltime(self, master_ms: int, timeout: float = 15.0) -> None:
        loop = asyncio.get_running_loop()
        self._polltime_future = loop.create_future()
        self._polltime_pending = master_ms
        self._polltime_sent = False
        self._tx_event.set()
        await asyncio.wait_for(self._polltime_future, timeout)

    async def send_nutzdaten(self, data: bytes, timeout: float = 15.0) -> None:
        if not self.established:
            raise S2Error("session not established")
        if not 0 < len(data) <= 247:
            raise ValueError("invalid Nutzdaten length")
        fut = asyncio.get_running_loop().create_future()
        self._tx_queue.append(_TxItem(bytes(data), fut))
        self._tx_event.set()
        await asyncio.wait_for(fut, timeout)

    async def close(self) -> None:
        if self._task and not self._task.done() and self.established:
            self._closing = True
            self._tx_event.set()
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 6)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
        if self._task and not self._task.done():
            self._task.cancel()
        self._close_socket()
        self.established = False

    # ------------------------------------------------------------ internals

    def _close_socket(self) -> None:
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self._writer:
            self._writer.close()
            self._writer = None

    async def _read_frame(self) -> tuple[int, bytes]:
        assert self._reader
        head = await self._reader.readexactly(4)
        key_number, length = struct.unpack(">HH", head)
        payload = await self._reader.readexactly(length)
        return key_number, payload

    async def _write(self, telegram: Telegram) -> None:
        assert self._writer
        _LOGGER.debug("TX %s", telegram)  # never log the payload, see Telegram.__str__
        self._writer.write(encode_frame(telegram, self._key_number, self._key))
        await self._writer.drain()

    async def _send_new(self, ik: int, nk: int, data: bytes = b"") -> None:
        self._tc = (self._tc + 1) & 0xFFFFFFFF
        self._rc = (self._rc + 1) & 0xFFFFFFFF
        telegram = Telegram(self._tc, self._rc, ik, PK_TELENOT, nk, self._version, data)
        self._last_sent = telegram
        await self._write(telegram)

    async def _synchronize(self) -> None:
        key_number, payload = await asyncio.wait_for(self._read_frame(), RECEIVE_TIMEOUT)
        if key_number != KEY_PLAIN:
            raise S2KeyError(f"expected plain SYNC_REQUEST, got key number {key_number}")
        sync = decode_payload(KEY_PLAIN, payload, None)
        if sync is None or sync.ik != IK_SYNC_REQUEST:
            raise S2Error(f"expected SYNC_REQUEST, got {sync}")
        _LOGGER.debug("RX %s", sync)
        self._check_window(sync)
        self._key = self._key_config.session_key(sync.tc)
        self._key_number = self._key_config.key_number
        self._rc = sync.tc
        await self._send_new(IK_SYNC_RESPONSE, NK_USERDATA_VDS, b"\x01")

        repeats = 0
        while True:
            key_number, payload = await asyncio.wait_for(self._read_frame(), RECEIVE_TIMEOUT)
            if key_number == KEY_PLAIN:
                tel = decode_payload(KEY_PLAIN, payload, None)
                if tel and tel.ik == IK_SYNC_REQUEST:
                    _LOGGER.debug("RX (repeat) %s", tel)
                    self._check_window(tel)
                    repeats += 1
                    if repeats > 2:
                        raise S2KeyError(
                            "Die Anlage wiederholt den Verbindungsaufbau – Freischaltcode prüfen"
                        )
                    if tel.tc == (self._rc - 1) & 0xFFFFFFFF and self._last_sent:
                        await self._write(self._last_sent)
                    else:
                        self._rc = tel.tc
                        await self._send_new(IK_SYNC_RESPONSE, NK_USERDATA_VDS, b"\x01")
                    continue
                raise S2KeyError("unexpected plain telegram during synchronisation")
            if key_number != self._key_number:
                raise S2KeyError(f"unexpected key number {key_number}")
            tel = decode_payload(key_number, payload, self._key)
            if tel is None:
                continue
            _LOGGER.debug("RX %s", tel)
            if tel.ik in (IK_ERROR_UNKNOWN_IK, IK_ERROR_UNKNOWN_PK):
                raise S2Error(f"panel reported protocol error IK={tel.ik}")
            if tel.ik != IK_POLLING:
                raise S2Error(f"unexpected telegram during synchronisation: {tel}")
            if tel.nk == NK_ERROR_VERSION_MISMATCH and 1 <= tel.v < self._version:
                _LOGGER.debug("Panel requests protocol version %s", tel.v)
                self._version = tel.v
            elif tel.nk != NK_USERDATA_VDS:
                raise S2Error(f"panel rejected session: NK={tel.nk}")
            verdict = self._verify(tel)
            if verdict == "accept":
                return
            if verdict == "resend" and self._last_sent:
                await self._write(self._last_sent)

    @staticmethod
    def _check_window(sync: Telegram) -> None:
        window = sync.data[0] if sync.data else 0
        if window == 0xFF:
            raise S2RemoteBusy(
                "Fernzugriff ist bereits belegt (läuft die BuildSec-App noch?)"
            )
        if window != 1:
            raise S2Error(f"unsupported window size {window}")

    def _verify(self, tel: Telegram) -> str:
        if tel.rc == (self._tc + 1) & 0xFFFFFFFF:
            return "accept" if tel.tc == self._rc else "ignore"
        if tel.rc == self._tc:
            return "resend" if tel.tc == (self._rc - 1) & 0xFFFFFFFF else "ignore"
        return "ignore"

    async def _receive_accepted(self) -> Telegram:
        """Wait for the next accepted telegram; may send a SERVICE_REQUEST meanwhile."""
        service_request_sent = False
        while True:
            if self._read_task is None:
                self._read_task = asyncio.create_task(self._read_frame())
            wait_for: set[asyncio.Future] = {self._read_task}
            event_task = None
            if not service_request_sent and not self._tx_event.is_set():
                event_task = asyncio.create_task(self._tx_event.wait())
                wait_for.add(event_task)
            done, _ = await asyncio.wait(
                wait_for, timeout=RECEIVE_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
            )
            if event_task and not event_task.done():
                event_task.cancel()
            if not done:
                raise S2Error("Zeitüberschreitung – die Anlage antwortet nicht")
            if self._read_task not in done:
                # Something to send while waiting for the master: ask it to poll us.
                if not service_request_sent and self._last_sent is not None:
                    service_request_sent = True
                    sr = Telegram(self._tc, self._rc, IK_SERVICE_REQUEST, PK_TELENOT,
                                  NK_USERDATA_VDS, self._version)
                    await self._write(sr)
                continue
            key_number, payload = self._read_task.result()
            self._read_task = None
            if key_number != self._key_number:
                if key_number == KEY_UNKNOWN:
                    raise S2KeyError("Die Anlage meldet eine unbekannte Schlüsselnummer")
                _LOGGER.debug("Ignoring telegram with key number %s", key_number)
                continue
            tel = decode_payload(key_number, payload, self._key)
            if tel is None:
                continue
            _LOGGER.debug("RX %s", tel)
            if tel.ik == IK_SYNC_REQUEST:
                raise S2Error("panel restarted the session")
            verdict = self._verify(tel)
            if verdict == "accept":
                return tel
            if verdict == "resend" and self._last_sent:
                _LOGGER.debug("Resending last telegram")
                await self._write(self._last_sent)

    async def _send_reply(self) -> bool:
        """Send exactly one telegram. Returns False if the session is finished."""
        self._tx_event.clear()
        if self._closing:
            if self._version >= 3:
                await self._send_new(IK_POLLING, NK_SESSION_CLOSE)
            return False
        if self._polltime_pending is not None and not self._polltime_sent:
            self._polltime_sent = True
            await self._send_new(IK_DATA, NK_POLLTIME_SET, struct.pack(">H", self._polltime_pending))
            return True
        if self._polltime_pending is None and self._tx_queue:
            item = self._tx_queue.popleft()
            await self._send_new(IK_DATA, NK_USERDATA_VDS, item.data)
            if not item.future.done():
                item.future.set_result(None)
            if self._tx_queue:
                self._tx_event.set()
            return True
        await self._send_new(IK_POLLING, NK_USERDATA_VDS)
        return True

    def _process(self, tel: Telegram) -> None:
        if tel.ik in (IK_ERROR_UNKNOWN_IK, IK_ERROR_UNKNOWN_PK):
            raise S2Error(f"panel reported protocol error IK={tel.ik}")
        if tel.nk == NK_SESSION_CLOSE:
            raise S2Error("panel closed the session")
        if tel.nk in (NK_ERROR_SESSION_CANCELED, NK_ERROR_INVALID_ID, NK_ERROR_VERSION_MISMATCH):
            raise S2Error(f"panel cancelled the session (NK=0x{tel.nk:02X})")
        if tel.ik == IK_POLLING and tel.nk == NK_POLLTIME_ACK:
            self._polltime_pending = None
            if self._polltime_future and not self._polltime_future.done():
                self._polltime_future.set_result(None)
            return
        if tel.ik == IK_DATA and tel.nk == NK_USERDATA_VDS and tel.data:
            try:
                self._on_nutzdaten(tel.data)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error while handling Nutzdaten (%d bytes)", len(tel.data))

    async def _run(self) -> None:
        error: Exception | None = None
        try:
            while await self._send_reply():
                tel = await self._receive_accepted()
                self._process(tel)
        except asyncio.CancelledError:
            raise
        except (S2Error, OSError, asyncio.IncompleteReadError) as err:
            error = err
            _LOGGER.debug("S2 session ended: %s", err)
        finally:
            self.established = False
            self._close_socket()
            exc = error or S2Error("session closed")
            for item in self._tx_queue:
                if not item.future.done():
                    item.future.set_exception(exc)
            self._tx_queue.clear()
            if self._polltime_future and not self._polltime_future.done():
                self._polltime_future.set_exception(exc)
            if self._on_closed:
                self._on_closed(None if self._closing else error)
