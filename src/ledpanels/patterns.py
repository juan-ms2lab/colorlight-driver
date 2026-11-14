"""Test patterns for identifying panel layout and orientation."""

import colorsys
import time

import cv2
import numpy as np

from .timing import hold, pause, running


def border(display):
    """White border, corners marked: top-left red, top-right green, bottom-left blue, bottom-right yellow."""
    c = display.canvas
    c[:] = 0
    c[0, :] = c[-1, :] = c[:, 0] = c[:, -1] = 255
    c[0, 0] = (255, 0, 0)
    c[0, -1] = (0, 255, 0)
    c[-1, 0] = (0, 0, 255)
    c[-1, -1] = (255, 255, 0)
    display.show()


def tiles(display, size: int = 128):
    """Number each size x size tile with a frame and a large digit to reveal flips and rotations."""
    display.clear()
    for i, x in enumerate(range(0, display.width, size)):
        cv2.rectangle(display.canvas, (x, 0), (x + size - 1, size - 1), (0, 80, 255), 1)
        cv2.putText(display.canvas, str(i + 1), (x + 30, size - 25), cv2.FONT_HERSHEY_SIMPLEX,
                    3, (255, 255, 255), 6)
        cv2.putText(display.canvas, "TOP", (x + 8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 1)
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


STATIC = {"border": border, "ruler": ruler, "tiles": tiles, "diagonal": diagonal}
ANIMATED = {"rainbow": rainbow, "sweep": sweep}
NAMES = sorted(STATIC) + sorted(ANIMATED)


def run_pattern(name: str, display, stop=None, deadline=None, **kw):
    """Draw a pattern; static ones are held until stop or deadline."""
    display.clear()
    if name in STATIC:
        STATIC[name](display, **kw)
        hold(display.show, stop, deadline)
    else:
        ANIMATED[name](display, stop=stop, deadline=deadline)
