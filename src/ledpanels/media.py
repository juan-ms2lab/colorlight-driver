"""Image and video helpers that fit media onto the display canvas."""

import os
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence

from .timing import pause, running

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".avi", ".mov", ".webm", ".gif"}


def fit(rgb: np.ndarray, width: int, height: int, mode: str = "contain") -> np.ndarray:
    """Resize an RGB image to the canvas. mode: contain, cover or stretch."""
    h, w = rgb.shape[:2]
    if mode == "stretch":
        return cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    scale = (min if mode == "contain" else max)(width / w, height / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    scaled = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((height, width, 3), np.uint8)
    # Center; negative offsets crop for "cover".
    ox, oy = (width - nw) // 2, (height - nh) // 2
    sx, sy = max(0, -ox), max(0, -oy)
    dx, dy = max(0, ox), max(0, oy)
    cw, ch = min(nw - sx, width - dx), min(nh - sy, height - dy)
    out[dy : dy + ch, dx : dx + cw] = scaled[sy : sy + ch, sx : sx + cw]
    return out


def load_image(path: str, width: int, height: int, mode: str = "contain") -> np.ndarray:
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"cannot read image: {path}")
    return fit(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), width, height, mode)


def first_frame(path: str) -> np.ndarray:
    """First frame of an image, GIF or video as RGB."""
    p = Path(path)
    if p.suffix.lower() == ".gif":
        with Image.open(p) as im:
            return np.asarray(im.convert("RGB")).copy()
    if p.suffix.lower() in IMAGE_EXT:
        bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
    else:
        cap = cv2.VideoCapture(str(p))
        ok, bgr = cap.read()
        cap.release()
        bgr = bgr if ok else None
    if bgr is None:
        raise ValueError(f"cannot decode: {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def play_gif(display, path: str, loop: bool = False, mode: str = "contain", stop=None, deadline=None):
    """Play an animated GIF using its own per-frame delays."""
    with Image.open(path) as im:
        while running(stop, deadline):
            for frame in ImageSequence.Iterator(im):
                if not running(stop, deadline):
                    return
                began = time.monotonic()
                delay = max(0.02, frame.info.get("duration", 100) / 1000)
                rgb = np.asarray(frame.convert("RGB"))
                display.show(fit(rgb, display.width, display.height, mode))
                pause(stop, max(0.0, delay - (time.monotonic() - began)))
            if not loop:
                return


def play_video(display, path: str, loop: bool = False, mode: str = "contain",
               max_fps: float = 60, stop=None, deadline: float | None = None):
    if Path(path).suffix.lower() == ".gif":
        return play_gif(display, path, loop, mode, stop, deadline)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    fps = min(cap.get(cv2.CAP_PROP_FPS) or 30, max_fps)
    period = 1.0 / fps
    next_t = time.monotonic()
    got_frame = False
    try:
        while running(stop, deadline):
            ok, bgr = cap.read()
            if not ok:
                # Rewind only if the last pass produced frames, so a broken file cannot spin.
                if loop and got_frame and cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                    got_frame = False
                    continue
                return
            got_frame = True
            next_t += period
            if time.monotonic() > next_t + period:
                continue  # running late, drop this frame
            display.show(fit(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), display.width, display.height, mode))
            delay = next_t - time.monotonic()
            if delay > 0:
                pause(stop, delay)
    finally:
        cap.release()


def _paced(display, grab, mode, max_fps, stop, deadline):
    """Show frames from grab() (returns RGB or None) at up to max_fps."""
    period = 1.0 / max_fps
    while running(stop, deadline):
        began = time.monotonic()
        rgb = grab()
        if rgb is None:
            raise ValueError("the video source stopped delivering frames")
        display.show(fit(rgb, display.width, display.height, mode))
        pause(stop, max(0.0, period - (time.monotonic() - began)))


def play_camera(display, device: int = 0, mode: str = "cover", mirror: bool = True,
                max_fps: float = 30, stop=None, deadline=None):
    cap = cv2.VideoCapture(int(device))
    if not cap.isOpened():
        raise ValueError(f"cannot open camera {device}")

    def grab():
        ok, bgr = cap.read()
        if not ok:
            return None
        if mirror:
            bgr = cv2.flip(bgr, 1)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    try:
        _paced(display, grab, mode, max_fps, stop, deadline)
    finally:
        cap.release()


def play_screen(display, monitor: int = 1, region=None, mode: str = "contain",
                max_fps: float = 20, stop=None, deadline=None):
    """Mirror a monitor or a [x, y, w, h] region of this computer's screen (X11 only)."""
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        raise ValueError("Screen mirroring needs an X11 session; Wayland blocks screen capture")
    import mss

    with mss.mss() as sct:
        if region:
            x, y, w, h = region
            area = {"left": x, "top": y, "width": w, "height": h}
        elif 0 <= monitor < len(sct.monitors):
            area = sct.monitors[monitor]
        else:
            raise ValueError(f"no monitor {monitor}")
        _paced(display, lambda: np.ascontiguousarray(np.asarray(sct.grab(area))[:, :, [2, 1, 0]]),
               mode, max_fps, stop, deadline)
