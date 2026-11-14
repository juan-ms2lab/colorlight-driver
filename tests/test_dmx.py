import socket
import struct
import time

import numpy as np
import pytest

from ledpanels import dmx


def sacn_packet(universe, data, priority=100, cid=b"\x01" * 16, name="test", options=0):
    n = len(data) + 1
    pkt = bytearray(126 + len(data))
    pkt[0:2] = struct.pack(">H", 0x10)
    pkt[4:16] = b"ASC-E1.17\x00\x00\x00"
    pkt[18:22] = struct.pack(">I", 4)
    pkt[22:38] = cid
    pkt[40:44] = struct.pack(">I", 2)
    pkt[44 : 44 + len(name)] = name.encode()
    pkt[108] = priority
    pkt[112] = options
    pkt[113:115] = struct.pack(">H", universe)
    pkt[117] = 2
    pkt[123:125] = struct.pack(">H", n)
    pkt[126:] = data
    return bytes(pkt)


def artnet_packet(universe, data):
    return (b"Art-Net\x00" + struct.pack("<H", 0x5000) + b"\x00\x0e\x00\x00"
            + bytes([universe & 255, universe >> 8]) + struct.pack(">H", len(data)) + data)


def make(**kw):
    return dmx.DmxInput({"width": 4, "height": 2, **kw})


def test_builders_round_trip():
    data = bytes(range(60)) + bytes(452)
    uni, got, prio, cid, name = dmx.parse_sacn(dmx.build_sacn(300, data, priority=77, name="x"))
    assert (uni, got, prio, name) == (300, data, 77, "x")
    assert dmx.parse_artnet(dmx.build_artnet(0x0105, data)) == (0x0105, data)


def test_parse_sacn():
    uni, data, prio, cid, name = dmx.parse_sacn(sacn_packet(7, bytes([1, 2, 3]), 90, name="desk"))
    assert (uni, data, prio, name) == (7, bytes([1, 2, 3]), 90, "desk")
    assert dmx.parse_sacn(b"junk" * 50) is None
    assert dmx.parse_sacn(sacn_packet(1, b"\x01", options=0x40)) is None


def test_parse_artnet():
    assert dmx.parse_artnet(artnet_packet(0x0102, b"\x09\x08")) == (0x0102, b"\x09\x08")
    assert dmx.parse_artnet(b"Art-Net\x00" + struct.pack("<H", 0x2000) + b"\0" * 20) is None


def test_linear_mapping_and_universe_count():
    inp = make(width=100, height=2)  # 200 px = 600 channels = 2 universes
    assert dmx.universe_count(inp.cfg) == 2
    inp._apply(1, bytes(range(30)), 0, "x", "x")
    inp._apply(2, bytes([200, 201, 202]), 0, "x", "x")
    g = inp.grid()
    assert g[0, 0].tolist() == [0, 1, 2]
    assert g[0, 9].tolist() == [27, 28, 29]
    assert g[1, 70].tolist() == [200, 201, 202]  # pixel 170 = row 1, col 70


def test_per_row_serpentine_and_order():
    inp = make(layout="per_row", serpentine=True, order="GRB")
    inp._apply(1, bytes([1, 2, 3] * 4), 0, "x", "x")
    inp._apply(2, bytes([10, 20, 30, 40, 50, 60] + [0] * 6), 0, "x", "x")
    g = inp.grid()
    assert g[0, 0].tolist() == [2, 1, 3]  # GRB source slots -> RGB
    assert g[1, 3].tolist() == [20, 10, 30]  # odd row is reversed
    assert g[1, 2].tolist() == [50, 40, 60]


def test_universe_below_start_ignored():
    inp = make(start_universe=5)
    inp._apply(4, bytes([9] * 12), 0, "x", "x")
    assert not inp.grid().any() and inp.version == 0


def test_priority_arbitration():
    inp = make()
    inp._on_sacn(sacn_packet(1, bytes([100] * 12), priority=100, cid=b"A" * 16), "1.1.1.1")
    inp._on_sacn(sacn_packet(1, bytes([5] * 12), priority=50, cid=b"B" * 16), "2.2.2.2")
    assert inp.grid()[0, 0, 0] == 100
    inp._on_sacn(sacn_packet(1, bytes([7] * 12), priority=100, cid=b"A" * 16), "1.1.1.1")
    assert inp.grid()[0, 0, 0] == 7


@pytest.mark.parametrize("cfg", [
    {"protocols": []}, {"width": 0}, {"layout": "x"}, {"order": "RGG"}, {"scale": "x"},
    {"interface_ip": "nope"}, {"layout": "per_row", "width": 200}, {"start_universe": -1},
])
def test_bad_config(cfg):
    with pytest.raises(ValueError):
        dmx.validate_config(cfg)


def test_scale_nearest():
    g = np.zeros((2, 4, 3), np.uint8)
    g[0, 0] = 255
    out = dmx.scale_nearest(g, 40, 20, "stretch")
    assert out.shape == (20, 40, 3) and out[0, 0].tolist() == [255] * 3 and out[0, 10].tolist() == [0] * 3
    boxed = dmx.scale_nearest(g, 40, 40, "contain")
    assert boxed[0].sum() == 0 and boxed[10, 0].tolist() == [255] * 3


def test_udp_end_to_end():
    inp = make(protocols=["artnet"])
    inp.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(artnet_packet(1, bytes([42] * 12)), ("127.0.0.1", dmx.ARTNET_PORT))
        end = time.monotonic() + 2
        while inp.version == 0 and time.monotonic() < end:
            time.sleep(0.01)
        assert inp.grid()[0, 0].tolist() == [42, 42, 42]
        assert inp.status()["sources"][0]["source"].startswith("artnet:")
    finally:
        inp.stop()
