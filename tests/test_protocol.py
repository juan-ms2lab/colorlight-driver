import numpy as np

from ledpanels import protocol as p


def test_fixed_sizes():
    assert len(p.build_brightness(255)) == 77
    assert len(p.build_sync(255)) == 112
    assert len(p.build_discover(0)) == 284


def test_discover_index_offset():
    assert p.build_discover(5)[16] == 5
    assert p.build_discover(5)[12] == 0x07


def test_sync_fields():
    pkt = p.build_sync(0x80)
    assert pkt[13] == 0x07
    assert pkt[13 + 22] == 0x80 and pkt[13 + 23] == 0x05
    assert pkt[13 + 25 : 13 + 28] == b"\x80\x80\x80"


def test_pixel_packet_layout():
    frame = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    pkts = list(p.pixel_packets(frame))
    assert len(pkts) == 2
    pkt = pkts[1]
    assert pkt[12] == 0x55
    assert pkt[13:15] == b"\x00\x01"  # row 1
    assert pkt[15:17] == b"\x00\x00"  # offset
    assert pkt[17:19] == b"\x00\x03"  # count
    assert pkt[19:21] == b"\x08\x88"
    assert pkt[21:] == frame[1].tobytes()


def test_wide_row_is_split_and_fits_mtu():
    frame = np.zeros((1, 600, 3), np.uint8)
    pkts = list(p.pixel_packets(frame))
    assert [int.from_bytes(k[17:19], "big") for k in pkts] == [497, 103]
    assert int.from_bytes(pkts[1][15:17], "big") == 497
    assert all(len(k) - 14 <= 1500 for k in pkts)


def test_parse_reply():
    buf = bytearray(1070)
    buf[12], buf[13] = 0x08, 0x05
    d = 13
    buf[d + 2], buf[d + 3] = 13, 4
    buf[d + 21 : d + 25] = bytes([0, 64, 0, 32])
    buf[d + 85] = 3
    r = p.parse_reply(bytes(buf))
    assert (r.id, r.fw_major, r.fw_minor, r.width, r.height) == (3, 13, 4, 64, 32)
    assert p.parse_reply(b"\0" * 20) is None
