"""Playback engine: one job at a time on a worker thread, plus playlist, schedule and saved state."""

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import animations, dmx, fonts, media, patterns
from .timing import hold, pause, running

KINDS = {"image", "video", "pattern", "text", "clock", "fill", "paint", "playlist", "camera", "dmx"}
FITS = {"contain", "cover", "stretch"}
DEFAULT_ITEM_SECONDS = 10
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def media_path(media_dir: Path, name: str) -> Path:
    p = Path(media_dir) / Path(str(name)).name
    if not p.is_file():
        raise ValueError(f"no such media file: {name}")
    return p


def validate(item, media_dir, in_playlist: bool = False):
    """Raise ValueError if a job dict is malformed."""
    if not isinstance(item, dict) or item.get("kind") not in KINDS:
        raise ValueError("job needs a valid 'kind'")
    kind = item["kind"]
    if item.get("duration") is not None:
        d = item["duration"]
        if isinstance(d, bool) or not isinstance(d, (int, float)) or not 0 < d <= 86400:
            raise ValueError("duration must be between 0 and 86400 seconds")
    if "color" in item:
        c = item["color"]
        if not (isinstance(c, list) and len(c) == 3
                and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in c)):
            raise ValueError("color must be [r, g, b] with values 0-255")
    if item.get("fit", "contain") not in FITS:
        raise ValueError("fit must be contain, cover or stretch")
    if kind in ("text", "clock"):
        if not fonts.is_valid(item.get("font", fonts.DEFAULT)):
            raise ValueError("unknown font")
        size = item.get("size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or not 4 <= size <= 400):
            raise ValueError("size must be a whole number of pixels, 4-400")
    if kind in ("image", "video"):
        media_path(media_dir, item.get("name", ""))
    elif kind == "pattern":
        if item.get("name") not in patterns.NAMES:
            raise ValueError(f"pattern must be one of {patterns.NAMES}")
    elif kind == "text":
        t = item.get("text")
        if not isinstance(t, str) or not 0 < len(t) <= 200:
            raise ValueError("text must be 1-200 characters")
        s = item.get("speed", 80)
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not 1 <= s <= 2000:
            raise ValueError("speed must be 1-2000 pixels per second")
    elif kind == "clock":
        f = item.get("fmt", "%H:%M:%S")
        if not isinstance(f, str) or not 0 < len(f) <= 32:
            raise ValueError("fmt must be a short strftime format")
    elif kind == "paint" and in_playlist:
        raise ValueError("paint cannot be part of a playlist")
    elif kind == "dmx":
        dmx.validate_config(item.get("config", {}))
    elif kind == "camera":
        dev = item.get("device", 0)
        if isinstance(dev, bool) or not isinstance(dev, int) or not 0 <= dev <= 9:
            raise ValueError("camera device must be 0-9")
    elif kind == "playlist":
        if in_playlist:
            raise ValueError("playlists cannot be nested")
        items = item.get("items")
        if not isinstance(items, list) or not 0 < len(items) <= 200:
            raise ValueError("playlist needs 1-200 items")
        for sub in items:
            validate(sub, media_dir, in_playlist=True)


def run_item(display, item, media_dir, stop, in_playlist: bool = False, wake=None):
    """Run one validated job until it ends, `stop` is set or its duration passes.

    `wake` (an Event) lets a paint job show a new live frame at once instead of on its next resend.
    """
    kind = item["kind"]
    duration = item.get("duration")
    if duration is None and in_playlist and (kind not in ("video",) or item.get("loop")):
        duration = DEFAULT_ITEM_SECONDS
    deadline = time.monotonic() + duration if duration else None
    color = item.get("color", [255, 255, 255])
    fit = item.get("fit", "contain")

    if kind == "image":
        path = media_path(media_dir, item["name"])
        display.canvas[:] = media.load_image(str(path), display.width, display.height, fit)
        hold(display.show, stop, deadline)
    elif kind == "video":
        path = media_path(media_dir, item["name"])
        media.play_video(display, str(path), item.get("loop", False), fit, stop=stop, deadline=deadline)
    elif kind == "pattern":
        patterns.run_pattern(item["name"], display, stop, deadline)
    elif kind == "text":
        animations.scroll_text(display, item["text"], color, item.get("speed", 80), stop, deadline,
                               item.get("font"), item.get("size"))
    elif kind == "clock":
        animations.clock(display, color, item.get("fmt", "%H:%M:%S"), stop, deadline,
                         item.get("font"), item.get("size"))
    elif kind == "dmx":
        dmx.run(display, item.get("config", {}), stop, deadline)
    elif kind == "camera":
        media.play_camera(display, item.get("device", 0), fit if "fit" in item else "cover",
                          item.get("mirror", True), stop=stop, deadline=deadline)
    elif kind == "fill":
        display.canvas[:] = color
        hold(display.show, stop, deadline)
    elif kind == "paint":
        wake = wake or threading.Event()
        while running(stop, deadline):
            display.show()
            wake.wait(0.05)
            wake.clear()
    elif kind == "playlist":
        while not stop.is_set():
            started = time.monotonic()
            for sub in item["items"]:
                if stop.is_set():
                    return
                run_item(display, sub, media_dir, stop, in_playlist=True)
            # A pass that ends at once (broken items) must not spin.
            if not item.get("loop", True) or time.monotonic() - started < 0.2:
                return


def in_window(on: str, off: str, now: datetime) -> bool:
    minutes = lambda s: int(s[:2]) * 60 + int(s[3:])
    a, b, n = minutes(on), minutes(off), now.hour * 60 + now.minute
    if a == b:
        return True
    return a <= n < b if a < b else (n >= a or n < b)


class Player:
    def __init__(self, display, media_dir, state_path=None):
        self.display = display
        self.media_dir = Path(media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        fonts.set_extra_dir(self.media_dir.parent / "fonts")
        self.state_path = Path(state_path) if state_path else None
        self.schedule = {"enabled": False, "on": "08:00", "off": "22:00"}
        self.playlist = {"items": [], "loop": True}
        self.dmx_cfg = dict(dmx.DEFAULT_CONFIG)
        self.brightness = 100
        self.gamma = 1.0
        self.balance = [100, 100, 100]
        self.autoplay = False
        self._last_job = None
        self._job = None
        self._ended = None
        self._error = None
        self._worker = None
        self._stop = threading.Event()
        self._painted = threading.Event()
        self.ddp = None  # ddp.DdpInput when enabled, for status
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._load_state()

    # ---- lifecycle

    def start(self):
        """Start the schedule supervisor and, if enabled, resume the last job."""
        self.set_brightness(self.brightness, save=False)
        self._apply_calibration()
        threading.Thread(target=self._supervise, daemon=True).start()
        if self.autoplay and self._last_job:
            try:
                self.play(self._last_job, remember=False)
            except ValueError as e:
                self._error = str(e)

    def close(self):
        self._closing.set()
        with self._lock:
            self._job = None
            self._halt()
            self._blank()

    # ---- control

    def play(self, job, remember: bool = True):
        validate(job, self.media_dir)
        if job["kind"] == "dmx" and "config" not in job:
            job = {**job, "config": self.dmx_cfg}
        with self._lock:
            self._job, self._ended = job, None
            if remember and job["kind"] != "paint":
                self._last_job = job
                self._save()
            self._launch()

    def stop(self):
        with self._lock:
            self._job = None
            self._halt()
            self._blank()

    def paint_frame(self, frame: np.ndarray):
        """Show a full (H, W, 3) frame, switching to live paint mode if needed."""
        with self._lock:
            if not (self._job and self._job["kind"] == "paint" and self._alive()):
                self._job, self._ended = {"kind": "paint"}, None
                self._launch()
            self.display.canvas[:] = frame
            self._painted.set()

    def end_paint(self):
        """Blank the panels if live paint is still what is showing (live input stopped)."""
        with self._lock:
            if self._job and self._job["kind"] == "paint":
                self.stop()

    def set_brightness(self, percent: int, save: bool = True):
        self.brightness = max(0, min(100, int(percent)))
        self.display.set_brightness(round(255 * self.brightness / 100))
        if save:
            self._save()

    def set_calibration(self, gamma: float, balance):
        if isinstance(gamma, bool) or not isinstance(gamma, (int, float)) or not 0.5 <= gamma <= 4:
            raise ValueError("gamma must be between 0.5 and 4")
        if not (isinstance(balance, (list, tuple)) and len(balance) == 3
                and all(isinstance(b, (int, float)) and not isinstance(b, bool) and 0 <= b <= 100 for b in balance)):
            raise ValueError("balance must be three percentages 0-100")
        self.gamma, self.balance = float(gamma), [float(b) for b in balance]
        self._apply_calibration()
        self._save()

    def _apply_calibration(self):
        self.display.set_calibration(self.gamma, tuple(b / 100 for b in self.balance))

    def set_schedule(self, enabled: bool, on: str, off: str):
        if not (_HHMM.match(str(on)) and _HHMM.match(str(off))):
            raise ValueError("times must be HH:MM")
        with self._lock:
            self.schedule = {"enabled": bool(enabled), "on": on, "off": off}
            self._save()

    def set_playlist(self, items, loop: bool = True):
        validate({"kind": "playlist", "items": items}, self.media_dir)
        with self._lock:
            self.playlist = {"items": items, "loop": bool(loop)}
            self._save()

    def set_autoplay(self, enabled: bool):
        self.autoplay = bool(enabled)
        self._save()

    def set_dmx(self, cfg):
        checked = dmx.validate_config(cfg)
        with self._lock:
            self.dmx_cfg = checked
            self._save()

    def status(self) -> dict:
        with self._lock:
            return {
                "job": self._job if self._alive() else None,
                "error": self._error,
                "asleep": self._asleep(),
                "schedule": self.schedule,
                "playlist": self.playlist,
                "brightness": self.brightness,
                "gamma": self.gamma,
                "balance": self.balance,
                "fps": round(self.display.fps, 1),
                "send_ms": round(self.display.send_ms, 1),
                "autoplay": self.autoplay,
                "dmx_config": self.dmx_cfg,
                "dmx": dmx.status(),
                "ddp": self.ddp.status() if self.ddp else None,
                "width": self.display.width,
                "height": self.display.height,
            }

    # ---- internals

    def _alive(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _asleep(self) -> bool:
        s = self.schedule
        return s["enabled"] and not in_window(s["on"], s["off"], datetime.now())

    def _blank(self):
        self.display.clear()
        try:
            self.display.show()
        except OSError:
            pass  # link is down; the retry loop restores output when it returns

    def _halt(self):
        """Stop the worker without touching the panels."""
        self._stop.set()
        w, self._worker = self._worker, None
        if w and w is not threading.current_thread():
            w.join()

    def _launch(self):
        self._halt()
        if self._job is None or self._asleep():
            return
        self._error = None
        stop = self._stop = threading.Event()
        self._worker = threading.Thread(target=self._work, args=(self._job, stop), daemon=True)
        self._worker.start()

    def _work(self, job, stop):
        # Must not take self._lock: callers hold it while joining this thread.
        try:
            while not stop.is_set():
                try:
                    run_item(self.display, job, self.media_dir, stop, wake=self._painted)
                    return
                except OSError as e:
                    # Cable pulled or interface gone: keep retrying until it comes back.
                    self._error = f"Link problem, retrying: {e}"
                    pause(stop, 2.0)
                    try:
                        self.display.reconnect()
                    except OSError:
                        pass
                    self._error = None
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
        finally:
            if not stop.is_set():
                self._ended = job
                self._blank()

    def _supervise(self):
        was_asleep = False
        while not self._closing.wait(1):
            with self._lock:
                asleep = self._asleep()
                if asleep and not was_asleep:
                    self._halt()
                    self._blank()
                elif was_asleep and not asleep and self._job and self._job is not self._ended:
                    self._launch()
                was_asleep = asleep

    def _load_state(self):
        if not (self.state_path and self.state_path.exists()):
            return
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        self.schedule = data.get("schedule", self.schedule)
        self.playlist = data.get("playlist", self.playlist)
        self.brightness = data.get("brightness", self.brightness)
        self.gamma = data.get("gamma", self.gamma)
        self.balance = data.get("balance", self.balance)
        self.autoplay = data.get("autoplay", self.autoplay)
        try:
            self.dmx_cfg = dmx.validate_config(data.get("dmx", self.dmx_cfg))
        except ValueError:
            pass
        self._last_job = data.get("last_job")

    def _save(self):
        if not self.state_path:
            return
        data = {"schedule": self.schedule, "playlist": self.playlist, "brightness": self.brightness,
                "gamma": self.gamma, "balance": self.balance, "dmx": self.dmx_cfg,
                "autoplay": self.autoplay, "last_job": self._last_job}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.state_path)
