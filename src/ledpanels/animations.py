"""Text-based animations. `display` needs width, height and show(frame)."""

import time
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import fonts
from .timing import pause, running


@lru_cache(maxsize=32)
def _truetype(path: str, cap_height: int):
    """Load a font sized so a capital letter is `cap_height` pixels tall."""
    probe = ImageFont.truetype(path, 100).getbbox("H")
    return ImageFont.truetype(path, max(4, round(cap_height * 100 / max(1, probe[3] - probe[1]))))


def render_text(text: str, height: int, color=(255, 255, 255), font: str | None = None,
                size: int | None = None) -> np.ndarray:
    """Render one line of text as a (height, w, 3) RGB strip. `size` is the capital letter height."""
    color = tuple(int(c) for c in color)
    font = font or fonts.DEFAULT
    cap_px = size or round(height * 0.6)
    if font in fonts.HERSHEY:
        hf = fonts.HERSHEY[font]
        text = text.encode("ascii", "replace").decode()
        cap = cv2.getTextSize("H", hf, 1, 1)[0][1]
        scale = cap_px / cap
        thick = max(1, round(scale * 1.2))
        width = cv2.getTextSize(text, hf, scale, thick)[0][0]
        img = np.zeros((height, width + 2, 3), np.uint8)
        cv2.putText(img, text, (1, (height + cap_px) // 2), hf, scale, color, thick, cv2.LINE_AA)
        return img
    f = _truetype(str(fonts.truetype_fonts()[font]), cap_px)
    img = Image.new("RGB", (int(f.getlength(text)) + 3, height))
    ImageDraw.Draw(img).text((1, (height + cap_px) // 2), text, font=f, fill=color, anchor="ls")
    return np.asarray(img).copy()


def scroll_text(display, text: str, color=(255, 255, 255), speed: float = 80,
                stop=None, deadline=None, font=None, size=None):
    """Scroll text right to left at `speed` pixels per second."""
    strip = render_text(text, display.height, color, font, size)
    gap = np.zeros((display.height, display.width, 3), np.uint8)
    full = np.hstack([gap, strip, gap])
    span = full.shape[1] - display.width
    pos, last = 0.0, time.monotonic()
    while running(stop, deadline):
        now = time.monotonic()
        pos += (now - last) * speed
        last = now
        if pos >= span:
            pos = 0.0
        x = int(pos)
        display.show(full[:, x : x + display.width])
        pause(stop, 1 / 30)


def clock(display, color=(255, 255, 255), fmt: str = "%H:%M:%S", stop=None, deadline=None,
          font=None, size=None):
    shown, frame = None, np.zeros((display.height, display.width, 3), np.uint8)
    while running(stop, deadline):
        text = time.strftime(fmt)
        if text != shown:
            strip = render_text(text, display.height, color, font, size)
            w = min(strip.shape[1], display.width)
            x0 = (display.width - w) // 2
            frame[:] = 0
            frame[:, x0 : x0 + w] = strip[:, :w]
            shown = text
        display.show(frame)
        pause(stop, 0.1)
