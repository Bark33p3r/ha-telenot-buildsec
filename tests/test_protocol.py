"""Self-contained tests: protocol helpers + full session against a simulated panel.

Run:  python tests/test_protocol.py
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "telenot_buildsec"))

from telenot import BuildSecClient, S2RemoteBusy  # noqa: E402
from telenot.buildsec import encode_kennwort, iter_saetze, st70  # noqa: E402
from telenot.s2 import (  # noqa: E402
    IK_DATA, IK_POLLING, IK_SERVICE_REQUEST, IK_SYNC_REQUEST, IK_SYNC_RESPONSE,
    KEY_BUILDSEC_BETREIBER, NK_POLLTIME_ACK, NK_POLLTIME_SET, NK_SESSION_CLOSE,
    NK_USERDATA_VDS, PK_VDS, Telegram, decode_payload, derive_betreiber_key,
    encode_frame, internet_checksum,
)

FREISCHALTCODE = "0011223344556677"
CODE = "123456"


def test_helpers() -> None:
    assert encode_kennwort("123456") == bytes([0x21, 0x43, 0x65])
    assert encode_kennwort("1234") == bytes([0x21, 0x43, 0xFF])
    assert encode_kennwort("12345") == bytes([0x21, 0x43, 0xF5])
    assert encode_kennwort("1") == bytes([0xF1, 0xFF, 0xFF])
    # RFC 1071 example
    assert internet_checksum(bytes.fromhex("0001f203f4f5f6f7")) == (~0xDDF2) & 0xFFFF
    key = derive_betreiber_key(bytes.fromhex(FREISCHALTCODE), 0x01020304)
    assert len(key) == 16
    assert key[0] == 0x00 ^ 0x01  # first byte xor MSB of tc
    # round trip encrypted
    tel = Telegram(5, 6, IK_DATA, data=b"\x01\x02\x03")
    frame = encode_frame(tel, KEY_BUILDSEC_BETREIBER, key)
    k, sl = struct.unpack(">HH", frame[:4])
    assert k == KEY_BUILDSEC_BETREIBER and sl == 256 and len(frame) == 260
    back = decode_payload(k, frame[4:], key)
    assert back and back.data == b"\x01\x02\x03" and back.tc == 5 and back.rc == 6
    # Satz parser
    assert list(iter_saetze(st70(0x10, b"\x92\x21\x43\x65\x00"))) == [
        bytes([6, 0x70, 0x10, 0x92, 0x21, 0x43, 0x65, 0x00])
    ]
    print("helpers ok")


def com2bus(*blocks: bytes) -> bytes:
    """Wrap Com2Bus blocks ([kennung, data...]) into a Satztyp 70 answer 0x99."""
    sets = b"".join(bytes([len(b) - 1]) + b for b in blocks)
    return st70(0x10, bytes([0x99, 0x00]) + sets)


def lcd(line1: str, line2: str) -> bytes:
    return (bytes([0x54, 0x1B, 0x80]) + line1.ljust(16).encode("latin-1")
            + bytes([0x1B, 0xC0]) + line2.ljust(16).encode("latin-1"))


class FakePanel:
    """Minimal S2 master that behaves like the complex 400H for BuildSec."""

    def __init__(self, busy: bool = False) -> None:
        self.busy = busy
        self.keys: list[int] = []
        self.armed = False
        self.got_session_close = False
        self.server: asyncio.base_events.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tc = 1000
        sync = Telegram(tc, 0, IK_SYNC_REQUEST, PK_VDS, data=b"\xff" if self.busy else b"\x01")
        writer.write(encode_frame(sync, 0, None))
        await writer.drain()
        if self.busy:
            writer.close()
            return
        key = derive_betreiber_key(bytes.fromhex(FREISCHALTCODE), tc)
        outbox: list[bytes] = []
        next_nk = NK_USERDATA_VDS

        async def read() -> Telegram:
            while True:
                k, sl = struct.unpack(">HH", await reader.readexactly(4))
                tel = decode_payload(k, await reader.readexactly(sl), key)
                assert tel is not None, "slave checksum wrong"
                if tel.ik == IK_SERVICE_REQUEST:
                    continue
                return tel

        slave = await read()
        assert slave.ik == IK_SYNC_RESPONSE and slave.rc == tc + 1
        try:
            while True:
                tc += 1
                if outbox:
                    tel = Telegram(tc, slave.tc + 1, IK_DATA, data=outbox.pop(0))
                else:
                    tel = Telegram(tc, slave.tc + 1, IK_POLLING, nk=next_nk)
                    next_nk = NK_USERDATA_VDS
                writer.write(encode_frame(tel, KEY_BUILDSEC_BETREIBER, key))
                await writer.drain()
                slave = await read()
                assert slave.rc == tc + 1, "slave RC wrong"
                if slave.nk == NK_SESSION_CLOSE:
                    self.got_session_close = True
                    break
                if slave.nk == NK_POLLTIME_SET:
                    assert slave.data == struct.pack(">H", 2000)
                    next_nk = NK_POLLTIME_ACK
                elif slave.ik == IK_DATA:
                    outbox.extend(self.answer(slave.data))
                await asyncio.sleep(0.02)
        except asyncio.IncompleteReadError:
            pass
        writer.close()

    def answer(self, data: bytes) -> list[bytes]:
        if len(data) == 1:
            self.keys.append(data[0])
            out = [com2bus(bytes([0xCE, data[0]]))]
            if bytes(self.keys[-7:]) == b"123456E":
                self.armed = True
            if bytes(self.keys[-7:]) == b"123456A":
                self.armed = False
            out.append(com2bus(
                lcd("Extern scharf" if self.armed else "Unscharf", ""),
                bytes([0xC0, 0x00, 0x01 | (0x20 if self.armed else 0x10), 0x00]),
            ))
            return out
        kennung = data[3]
        if kennung == 0x92:
            assert data[4:7] == encode_kennwort(CODE), "wrong code"
            return [st70(0x10, b"\x93\x00\x12\x34"),
                    com2bus(lcd("Unscharf", "Bereit"), bytes([0xC0, 0x00, 0x11, 0x00]))]
        if kennung == 0x18:
            return [st70(0x00, bytes([0x19, 0x05, 38, 88, 0]) + bytes(12))]
        if kennung == 0x98:
            def label(idx: int, text: str) -> bytes:
                raw = text.encode("latin-1")
                return bytes([0xCD, 0x01, idx, len(raw), 0]) + raw
            return [
                com2bus(label(0, "Extern scharf"), label(1, "Intern scharf"), label(4, "Haustür")),
                com2bus(bytes([0xCD, 0x02, 0xAB, 0xCD])),
            ]
        return []


async def test_session() -> None:
    panel = FakePanel()
    port = await panel.start()
    updates = []
    client = BuildSecClient("127.0.0.1", port, FREISCHALTCODE, CODE, 0,
                            on_update=lambda st: updates.append((st.line1, st.leds[0].color)))
    await asyncio.wait_for(client.connect(), 20)
    st = client.state
    assert client.connected
    assert st.firmware == "38.88.0", st.firmware
    assert st.leds[0].label == "Extern scharf" and st.leds[1].label == "Intern scharf"
    assert st.meldebereiche[0].label == "Haustür"
    assert st.labels_loaded
    await asyncio.sleep(0.3)
    assert st.line1 == "Unscharf" and st.betrieb and st.leds[0].color == "green", st
    await client.press_sequence("123456E", delay=0)
    await asyncio.sleep(0.3)
    assert panel.armed and st.line1 == "Extern scharf" and st.leds[0].color == "red", st
    await client.press_sequence("123456A", delay=0)
    await asyncio.sleep(0.3)
    assert not panel.armed and st.line1 == "Unscharf"
    await client.disconnect()
    await asyncio.sleep(0.2)
    assert panel.got_session_close
    print("session ok,", len(updates), "updates")

    busy = FakePanel(busy=True)
    port = await busy.start()
    client = BuildSecClient("127.0.0.1", port, FREISCHALTCODE, CODE)
    try:
        await client.connect()
        raise AssertionError("busy not detected")
    except S2RemoteBusy:
        print("busy ok")


if __name__ == "__main__":
    test_helpers()
    asyncio.run(test_session())
    print("ALL TESTS PASSED")
