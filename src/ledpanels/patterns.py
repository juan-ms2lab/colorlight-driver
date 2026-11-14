"""Test patterns for identifying panel layout and orientation."""

import colorsys
import socket
import time

import cv2
import numpy as np

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


def tiles(display, size: int = 128):
    """Number each tile (size x size, capped at the display height) of each display with a frame and a large
    digit to reveal flips, rotations and the display order."""
    display.clear()
    c, i = display.canvas, 0
    for rx, ry, rw, rh in _regions(display):
        s = min(size, rh)
        for x in range(rx, rx + rw, s):
            i += 1
            cv2.rectangle(c, (x, ry), (x + s - 1, ry + s - 1), (0, 80, 255), 1)
            cv2.putText(c, str(i), (x + s * 30 // 128, ry + s - s * 25 // 128), cv2.FONT_HERSHEY_SIMPLEX,
                        3 * s / 128, (255, 255, 255), max(1, 6 * s // 128))
            cv2.putText(c, "TOP", (x + 8, ry + max(12, 20 * s // 128)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6 * max(s, 64) / 128, (255, 255, 0), 1)
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
    font = cv2.FONT_HERSHEY_SIMPLEX
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
            (tw, th), _ = cv2.getTextSize(text, font, 1, 1)
            scale = min((w - 4) / tw, h * 0.6 / th)
            (tw, th), _ = cv2.getTextSize(text, font, scale, 1)
            cv2.putText(display.canvas, text, (x + (w - tw) // 2, y + (h + th) // 2), font, scale,
                        (255, 255, 255), max(1, round(scale)), cv2.LINE_AA)
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
