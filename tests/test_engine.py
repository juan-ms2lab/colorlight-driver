import time
from datetime import datetime

import numpy as np
import pytest

from ledpanels import engine
from ledpanels.sim import SimDisplay


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def player(tmp_path):
    p = engine.Player(SimDisplay(32, 8), tmp_path / "media", tmp_path / "state.json")
    yield p
    p.close()


def test_in_window_same_day_and_overnight():
    t = lambda h, m: datetime(2026, 1, 1, h, m)
    assert engine.in_window("08:00", "22:00", t(12, 0))
    assert not engine.in_window("08:00", "22:00", t(23, 0))
    assert engine.in_window("22:00", "06:00", t(23, 30))
    assert engine.in_window("22:00", "06:00", t(5, 59))
    assert not engine.in_window("22:00", "06:00", t(12, 0))


@pytest.mark.parametrize("job", [
    {"kind": "nope"},
    {"kind": "fill", "color": [1, 2]},
    {"kind": "fill", "color": [1, 2, 300]},
    {"kind": "fill", "duration": -1},
    {"kind": "text", "text": ""},
    {"kind": "pattern", "name": "../x"},
    {"kind": "image", "name": "missing.png"},
    {"kind": "playlist", "items": []},
    {"kind": "playlist", "items": [{"kind": "paint"}]},
])
def test_validate_rejects(job, tmp_path):
    with pytest.raises(ValueError):
        engine.validate(job, tmp_path)


def test_media_name_cannot_escape(tmp_path):
    (tmp_path / "secret.png").write_bytes(b"x")
    media = tmp_path / "media"
    media.mkdir()
    with pytest.raises(ValueError):
        engine.media_path(media, "../secret.png")


def test_fill_then_stop_blanks(player):
    player.play({"kind": "fill", "color": [9, 8, 7]})
    assert wait_for(lambda: player.display.canvas[0, 0].tolist() == [9, 8, 7])
    assert player.status()["job"]["kind"] == "fill"
    player.stop()
    assert not player.display.canvas.any()
    assert player.status()["job"] is None


def test_job_with_duration_ends_and_blanks(player):
    player.play({"kind": "fill", "color": [5, 5, 5], "duration": 0.2})
    assert wait_for(lambda: player.status()["job"] is None)
    assert wait_for(lambda: not player.display.canvas.any())


def test_playlist_runs_items_in_order(player):
    seen = []
    orig = player.display.show

    def spy(frame=None):
        seen.append(player.display.canvas[0, 0, 0])
        orig(frame)

    player.display.show = spy
    items = [{"kind": "fill", "color": [10, 0, 0], "duration": 0.25},
             {"kind": "fill", "color": [20, 0, 0], "duration": 0.25}]
    player.play({"kind": "playlist", "items": items, "loop": False})
    assert wait_for(lambda: player.status()["job"] is None, 5)
    first = seen.index(10)
    assert 20 in seen[first:]


def test_paint_frame_switches_to_paint(player):
    frame = np.full((8, 32, 3), 77, np.uint8)
    player.paint_frame(frame)
    assert player.status()["job"] == {"kind": "paint"}
    assert (player.display.canvas == 77).all()


def test_state_persists(tmp_path):
    p = engine.Player(SimDisplay(8, 8), tmp_path / "m", tmp_path / "s.json")
    p.set_brightness(40)
    p.set_schedule(True, "09:00", "17:30")
    p.close()
    q = engine.Player(SimDisplay(8, 8), tmp_path / "m", tmp_path / "s.json")
    assert q.brightness == 40
    assert q.schedule == {"enabled": True, "on": "09:00", "off": "17:30"}
    q.close()


def test_bad_schedule_time(player):
    with pytest.raises(ValueError):
        player.set_schedule(True, "25:00", "10:00")
