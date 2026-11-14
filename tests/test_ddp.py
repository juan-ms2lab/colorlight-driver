import socket
import struct
import time

import numpy as np
import pytest

from ledpanels import ddp, engine
from ledpanels.sim import SimDisplay

W, H = 40, 30  # 3600 bytes: three packets of 1440, 1440 and 720


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def live(tmp_path):
    player = engine.Player(SimDisplay(W, H), tmp_path / "media")
    inp = ddp.DdpInput(W, H, player.paint_frame, player.end_paint, "127.0.0.1", 0, timeout=0.5).start()
    player.ddp = inp
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    yield player, inp, lambda pkt: tx.sendto(pkt, ("127.0.0.1", inp.port))
    tx.close()
    inp.stop()
    player.close()


def test_packets_header_and_split():
    frame = np.arange(H * W * 3, dtype=np.uint32).astype(np.uint8).reshape(H, W, 3)
    pkts = ddp.packets(frame, seq=3)
    assert [p[:10] for p in pkts] == [
        bytes([0x40, 3, 0x0B, 1]) + struct.pack(">IH", 0, 1440),
        bytes([0x40, 3, 0x0B, 1]) + struct.pack(">IH", 1440, 1440),
        bytes([0x41, 3, 0x0B, 1]) + struct.pack(">IH", 2880, 720),
    ]
    assert b"".join(p[10:] for p in pkts) == frame.tobytes()


def test_parse_variants():
    assert ddp.parse(bytes([0x41, 1, 0x0B, 1]) + struct.pack(">IH", 6, 3) + b"abc") == (6, b"abc", True)
    timecode = bytes([0x50, 1, 0x0B, 1]) + struct.pack(">IH", 0, 2) + b"\0\0\0\0" + b"xy"
    assert ddp.parse(timecode) == (0, b"xy", False)
    assert ddp.parse(bytes([0x43, 1, 0x0B, 1]) + bytes(6)) is None  # query
    assert ddp.parse(bytes([0x41, 1, 0x0B, 251]) + bytes(6)) is None  # status/config id
    assert ddp.parse(b"\x40\x01") is None


def test_frame_over_udp_then_timeout_blanks(live):
    player, inp, send = live
    frame = np.random.default_rng(0).integers(0, 256, (H, W, 3), dtype=np.uint8)
    pkts = ddp.packets(frame)
    for p in pkts[:-1]:
        send(p)
    time.sleep(0.1)
    assert player.status()["job"] is None  # nothing shown before PUSH
    shown = player.display.frames
    send(pkts[-1])
    assert wait_for(lambda: (player.display.canvas == frame).all())
    assert player.status()["job"] == {"kind": "paint"}
    assert wait_for(lambda: player.display.frames > shown)
    st = player.status()["ddp"]
    assert st["live"] and st["frames"] == 1 and st["packets"] == 3 and st["source"] == "127.0.0.1"
    # No data for the timeout: the panels blank and paint mode ends.
    assert wait_for(lambda: player.status()["job"] is None and not player.display.canvas.any())
    assert not player.status()["ddp"]["live"]


def test_partial_update_and_oversize_is_clipped(live):
    player, inp, send = live
    send(bytes([0x41, 1, 0x0B, 1]) + struct.pack(">IH", 3, 3) + b"\xff\x00\x80")  # pixel (1, 0)
    assert wait_for(lambda: tuple(player.display.canvas[0, 1]) == (255, 0, 128))
    send(bytes([0x41, 2, 0x0B, 1]) + struct.pack(">IH", W * H * 3 - 3, 6) + b"\x01\x02\x03\x04\x05\x06")
    assert wait_for(lambda: tuple(player.display.canvas[-1, -1]) == (1, 2, 3))
    assert tuple(player.display.canvas[0, 1]) == (255, 0, 128)


def test_other_job_is_not_stopped_by_timeout(live):
    player, inp, send = live
    for p in ddp.packets(np.zeros((H, W, 3), np.uint8)):
        send(p)
    assert wait_for(lambda: inp.live)
    player.play({"kind": "fill", "color": [1, 2, 3]})
    assert wait_for(lambda: not inp.live)
    time.sleep(0.1)
    assert player.status()["job"]["kind"] == "fill"

