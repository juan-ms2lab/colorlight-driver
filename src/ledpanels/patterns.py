"""Test patterns for identifying panel layout and orientation."""

import colorsys
import socket
import time

import numpy as np

from . import pixelfont
from .timing import hold, pause, running

WEB_PORT = 8080  # shown by the ip pattern; `serve` sets the real port


def _regions(display):
    return getattr(display, "regions", None) or [(0, 0, display.width, display.height)]


def border(display):
    """White border around each display, corners marked: top-left red, top-right green, bottom-left blue,
    bottom-right yellow."""
    display.canvas[:] = 0
    for x, y, w, h in _regions(display):
        c = display.canvas[y : y + h, x : x + w]
        c[0, :] = c[-1, :] = c[:, 0] = c[:, -1] = 255
        c[0, 0] = (255, 0, 0)
        c[0, -1] = (0, 255, 0)
        c[-1, 0] = (0, 0, 255)
        c[-1, -1] = (255, 255, 0)
    display.show()


PANEL_COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 255, 255), (255, 0, 255),
                (255, 128, 0), (128, 0, 255)]  # 8, so stacked rows of 6 or 12 panels differ vertically
PANEL_WIDTH = 0  # `panel_width` / `panel_height` in ledpanels.toml; height 0 = the display's height
PANEL_HEIGHT = 0


def tiles(display, width=None, height=None):
    """Each panel its own color with a white seam on its top and left edges and its number, counted left to
    right, then top to bottom, continuing across displays, so swapped, flipped or misordered panels stand out."""
    display.clear()
    pw, ph = width or PANEL_WIDTH or 128, height or PANEL_HEIGHT
    c, i = display.canvas, 0
    for rx, ry, rw, rh in _regions(display):
        h = min(ph or rh, rh)
        for y in range(ry, ry + rh, h):
            for x in range(rx, rx + rw, pw):
                i += 1
                color = PANEL_COLORS[(i - 1) % len(PANEL_COLORS)]
                tile = c[y : min(y + h, ry + rh), x : min(x + pw, rx + rw)]
                tile[:] = color
                tile[0, :] = tile[:, 0] = 255
                th, tw = tile.shape[:2]
                k = max(1, min(th * 3 // 5 // 5, tw * 3 // 4 // pixelfont.text_width(str(i))))
                ink = (0, 0, 0) if sum(color) > 400 else (255, 255, 255)
                pixelfont.draw_text(tile, (tw - pixelfont.text_width(str(i), k)) // 2, (th - 5 * k) // 2, str(i),
                                    ink, k)
    display.show()


def ruler(display, block: int = 128):
    """Vertical color blocks (red, green, blue, yellow repeating) to measure the visible width."""
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for i, x in enumerate(range(0, display.width, block)):
        display.canvas[:, x : x + block] = colors[i % 4]
    display.show()


def diagonal(display, start: int = 640, step: int = 2):
    """A 3px-wide line running down and to the right; 16-row bands change color.

    Row y lights x = start + y * step, so the last visible band shows where the display ends.
    """
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
              (0, 255, 255), (255, 0, 255), (255, 255, 255), (255, 128, 0)]
    for y in range(display.height):
        x = start + y * step
        if x < display.width:
            display.canvas[y, x : x + 3] = colors[(y // 16) % 8]
    display.show()


def rainbow(display, stop=None, deadline=None):
    xs = np.linspace(0, 1, display.width, endpoint=False)
    while running(stop, deadline):
        hue = (xs + time.monotonic() * 0.2) % 1.0
        row = np.array([colorsys.hsv_to_rgb(h, 1, 1) for h in hue]) * 255
        display.show(np.broadcast_to(row.astype(np.uint8), (display.height, display.width, 3)))
        pause(stop, 1 / 30)


def sweep(display, stop=None, deadline=None):
    """A white row then a white column sweeping across, to verify pixel addressing."""
    while running(stop, deadline):
        for y in range(display.height):
            if not running(stop, deadline):
                return
            display.clear()
            display.canvas[y, :] = 255
            display.show()
            pause(stop, 0.05)
        for x in range(0, display.width, 4):
            if not running(stop, deadline):
                return
            display.clear()
            display.canvas[:, x : x + 4] = 255
            display.show()
            pause(stop, 0.05)


def ip(display, stop=None, deadline=None, port=None):
    """Hostname, IP address and web port on every display, so a headless setup can be found.

    Refreshed every 3 s because Wi-Fi may come up after the server starts at boot.
    """
    while running(stop, deadline):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # picks the outgoing interface; sends nothing
                addr = s.getsockname()[0]
        except OSError:
            addr = "no network"
        text = f"{socket.gethostname()} {addr}:{port or WEB_PORT}"
        display.clear()
        for x, y, w, h in _regions(display):
            k = max(1, min((w - 4) // pixelfont.text_width(text), h * 3 // 5 // 5))
            pixelfont.draw_text(display.canvas, x + (w - pixelfont.text_width(text, k)) // 2,
                                y + (h - 5 * k) // 2, text, (255, 255, 255), k)
        until = time.monotonic() + 3
        hold(display.show, stop, min(until, deadline) if deadline else until)


STATIC = {"border": border, "ruler": ruler, "tiles": tiles, "diagonal": diagonal}
ANIMATED = {"ip": ip, "rainbow": rainbow, "sweep": sweep}
NAMES = sorted(STATIC) + sorted(ANIMATED)


def run_pattern(name: str, display, stop=None, deadline=None, **kw):
    """Draw a pattern; static ones are held until stop or deadline."""
    display.clear()
    if name in STATIC:
        STATIC[name](display, **kw)
        hold(display.show, stop, deadline)
    else:
        ANIMATED[name](display, stop=stop, deadline=deadline, **kw)
