import numpy as np
import pytest

from ledpanels import patterns, protocol as p, sender
from ledpanels.sender import build_map

# The two-strip setup from the README: 768x64 canvas, top half at card x 0, bottom half at card x 768,
# both on card rows 160-191.
STRIPS = [{"canvas": [0, 0, 768, 32], "card": [0, 160]}, {"canvas": [0, 32, 768, 32], "card": [768, 160]}]


class FakeSock:
    def __init__(self):
        self.sent = []

    def send(self, pkt):
        self.sent.append(bytes(pkt))

    def close(self):
        pass


@pytest.fixture
def make(monkeypatch):
    monkeypatch.setattr(sender, "_tx_socket", lambda iface: FakeSock())
    monkeypatch.setattr(sender.time, "sleep", lambda s: None)

    def make(w, h, **kw):
        return sender.Display("x", w, h, color_order="RGB", **kw)
    return make


def pixel_pkts(d):
    return [k for k in d._sock.sent if k[12] == p.TYPE_PIXELS]


def head(pkt):
    return int.from_bytes(pkt[13:15], "big"), int.from_bytes(pkt[15:17], "big"), int.from_bytes(pkt[17:19], "big")


def random_frame(w, h):
    return np.random.default_rng(1).integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_default_config_unchanged(make):
    d = make(600, 3)
    frame = random_frame(600, 3)
    d.show(frame)
    sent = d._sock.sent
    assert sent[0] == p.build_brightness(255) and sent[-1] == p.build_sync(255)
    assert sent[1:-1] == list(p.pixel_packets(frame))
    assert d.regions == [(0, 0, 600, 3)]


def test_two_strips_merge_into_one_sweep_per_card_row(make):
    d = make(768, 64, displays=STRIPS)
    frame = random_frame(768, 64)
    d.show(frame)
    pkts = pixel_pkts(d)
    assert len(pkts) == 32 * 4  # one 1536-pixel run per card row: 497 + 497 + 497 + 45
    assert [head(k) for k in pkts[:4]] == [(160, 0, 497), (160, 497, 497), (160, 994, 497), (160, 1491, 45)]
    assert head(pkts[-1]) == (191, 1491, 45)
    assert pkts[0][:21] == p.DST_MAC + p.SRC_MAC + bytes([0x55, 0, 160, 0, 0, 1, 0xF1, 0x08, 0x88])
    for r in (0, 15, 31):
        row = b"".join(k[21:] for k in pkts[r * 4 : r * 4 + 4])
        assert row == frame[r].tobytes() + frame[32 + r].tobytes()
    assert d.regions == [(0, 0, 768, 32), (0, 32, 768, 32)]


def test_gap_between_displays_is_black_and_flips_apply():
    frame = np.arange(2 * 4 * 3, dtype=np.uint8).reshape(2, 4, 3)
    displays = [{"canvas": [0, 0, 2, 2], "card": [5, 0]},
                {"canvas": [2, 0, 2, 2], "card": [8, 0], "flip_x": True, "flip_y": True}]
    index, rows = build_map(displays, 4, 2)
    assert rows == [(0, 5, 0, 5), (1, 5, 5, 5)]
    flat = np.concatenate([frame.reshape(-1, 3), np.zeros((1, 3), np.uint8)])
    wire = flat[index]
    black = [0, 0, 0]
    assert wire[:5].tolist() == [frame[0, 0].tolist(), frame[0, 1].tolist(), black,
                                 frame[1, 3].tolist(), frame[1, 2].tolist()]


def test_reverse_panels():
    index, rows = build_map([{"canvas": [0, 0, 6, 1], "card": [0, 0], "reverse_panels": True, "panel_width": 2}], 6, 1)
    assert index.tolist() == [4, 5, 2, 3, 0, 1]


@pytest.mark.parametrize("bad", [
    {"canvas": [0, 0, 800, 32], "card": [0, 0]},
    {"canvas": [0, 0, 768, 32]},
    {"canvas": [0, 0, 100, 32], "card": [0, 0], "reverse_panels": True, "panel_width": 64},
])
def test_bad_display_entries(bad):
    with pytest.raises(ValueError):
        build_map([bad], 768, 64)


def test_border_marks_each_display(make):
    d = make(768, 64, displays=STRIPS)
    patterns.border(d)
    assert tuple(d.canvas[0, 0]) == (255, 0, 0) and tuple(d.canvas[31, 0]) == (0, 0, 255)
    assert tuple(d.canvas[32, 0]) == (255, 0, 0) and tuple(d.canvas[63, 767]) == (255, 255, 0)
    patterns.tiles(d)  # tiles shrink to the 32-row displays without drawing outside them


def test_display_entries_from_toml(tmp_path):
    from ledpanels import cli
    cfg = tmp_path / "ledpanels.toml"
    cfg.write_text('width = 768\nheight = 64\n\n[[display]]\ncanvas = [0, 0, 768, 32]\ncard = [0, 160]\n\n'
                   '[[display]]\ncanvas = [0, 32, 768, 32]\ncard = [768, 160]\n')
    s = cli.load_settings(cli.build_parser().parse_args(["-c", str(cfg), "clear"]))
    assert s["display"] == STRIPS
    assert cli.load_settings(cli.build_parser().parse_args(["-c", str(tmp_path / "none.toml"), "clear"]))["display"] == []


def test_sync_gap_from_config(tmp_path, make, monkeypatch):
    from ledpanels import cli
    cfg = tmp_path / "ledpanels.toml"
    cfg.write_text("sync_gap = 0.006\n")
    s = cli.load_settings(cli.build_parser().parse_args(["-c", str(cfg), "clear"]))
    assert s["sync_gap"] == 0.006
    slept = []
    monkeypatch.setattr(sender.time, "sleep", slept.append)
    make(4, 1, sync_gap=s["sync_gap"]).show()
    assert slept == [0.006]
