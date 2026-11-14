"""Colorlight sender-to-receiver raw Ethernet protocol (reverse engineered).

Frames use a fixed MAC pair. The one-byte packet type sits in the high byte of
the EtherType field; the first data byte is its low byte.
"""

import struct
from dataclasses import dataclass

import numpy as np

DST_MAC = bytes.fromhex("112233445566")
SRC_MAC = bytes.fromhex("222233445566")

TYPE_SYNC = 0x01
TYPE_DISCOVER = 0x07
TYPE_REPLY = 0x08
TYPE_BRIGHTNESS = 0x0A
TYPE_PIXELS = 0x55

ETH_P_REPLY = 0x0805  # type 0x08, first data byte 0x05 (5A card)
MAX_PIXELS_PER_PACKET = 497

_HEADER_LEN = 13  # two MACs + type byte


def _frame(ptype: int, data: bytes | bytearray, total_len: int) -> bytes:
    pkt = DST_MAC + SRC_MAC + bytes([ptype]) + bytes(data)
    return pkt.ljust(total_len, b"\0")


def build_brightness(level: int) -> bytes:
    return _frame(TYPE_BRIGHTNESS, bytes([level, level, level, 0xFF]), 77)


def build_sync(level: int) -> bytes:
    data = bytearray(99)
    data[0] = 0x07  # sent by PC/netcard
    data[22] = level
    data[23] = 0x05
    data[25:28] = bytes([level] * 3)
    return _frame(TYPE_SYNC, data, 112)


def build_discover(index: int) -> bytes:
    data = bytearray(271)
    data[3] = index
    return _frame(TYPE_DISCOVER, data, 284)


def pixel_packets(frame: np.ndarray):
    """Yield row packets for a contiguous (H, W, 3) uint8 frame in wire color order."""
    for y in range(frame.shape[0]):
        yield from row_packets(y, 0, frame[y])


def row_packets(y: int, x0: int, row: np.ndarray):
    """Yield the packets for one card row of (N, 3) pixels starting at card column x0."""
    prefix = DST_MAC + SRC_MAC + bytes([TYPE_PIXELS])
    for i in range(0, len(row), MAX_PIXELS_PER_PACKET):
        chunk = row[i : i + MAX_PIXELS_PER_PACKET]
        head = struct.pack(">HHHBB", y, x0 + i, len(chunk), 0x08, 0x88)
        yield prefix + head + chunk.tobytes()


@dataclass
class Receiver:
    id: int
    fw_major: int
    fw_minor: int
    width: int
    height: int
    packets: int
    uptime_ms: int


def parse_reply(buf: bytes) -> Receiver | None:
    if len(buf) < _HEADER_LEN + 86 or buf[12] != TYPE_REPLY:
        return None
    d = buf[_HEADER_LEN:]
    u32 = lambda o: struct.unpack(">I", d[o : o + 4])[0]
    return Receiver(
        id=d[85],
        fw_major=d[2],
        fw_minor=d[3],
        width=(d[21] << 8) | d[22],
        height=(d[23] << 8) | d[24],
        packets=u32(38),
        uptime_ms=u32(46),
    )
