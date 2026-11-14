"""Direct pixel control with the ledpanels API.

Run one demo at a time (stop the web panel first, only one program should send):
    .venv/bin/python examples/api_demo.py pixels
    .venv/bin/python examples/api_demo.py plasma
    .venv/bin/python examples/api_demo.py life
Add --simulate to run without hardware (no output is shown).
"""

import sys
import time

import numpy as np

from ledpanels import Display
from ledpanels.sim import SimDisplay

WIDTH, HEIGHT = 768, 128


def pixels(d):
    """Light single pixels one at a time along the diagonal, then a red/green/blue row."""
    for i in range(HEIGHT):
        d.set_pixel(i * 6, i, (255, 255, 255))
        d.show()
        time.sleep(0.02)
    d.canvas[HEIGHT // 2, 0:256] = (255, 0, 0)
    d.canvas[HEIGHT // 2, 256:512] = (0, 255, 0)
    d.canvas[HEIGHT // 2, 512:768] = (0, 0, 255)
    d.show()
    time.sleep(3)


def plasma(d, seconds=30):
    """Whole-canvas math: build a frame with numpy and send it. Any (H, W, 3) uint8 array works."""
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        t = time.monotonic()
        v = np.sin(x / 40 + t) + np.sin(y / 20 - t * 1.3) + np.sin((x + y) / 50 + t * 0.7)
        frame = np.stack([np.sin(v * 2) , np.sin(v * 2 + 2.1), np.sin(v * 2 + 4.2)], axis=-1)
        d.show(((frame + 1) * 127.5).astype(np.uint8))


def life(d, seconds=30, cell=4):
    """Conway's Game of Life on a coarse grid, scaled up by `cell` pixels."""
    rng = np.random.default_rng()
    grid = rng.random((HEIGHT // cell, WIDTH // cell)) < 0.25
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        n = sum(np.roll(np.roll(grid, i, 0), j, 1) for i in (-1, 0, 1) for j in (-1, 0, 1)) - grid
        grid = (n == 3) | (grid & (n == 2))
        img = np.kron(grid, np.ones((cell, cell), bool))
        d.show(img[..., None] * np.array([0, 255, 120], np.uint8))
        time.sleep(0.08)


DEMOS = {"pixels": pixels, "plasma": plasma, "life": life}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "pixels"
    if name not in DEMOS:
        sys.exit(f"usage: api_demo.py [{'|'.join(DEMOS)}] [--simulate]")
    if "--simulate" in sys.argv:
        display = SimDisplay(WIDTH, HEIGHT)
    else:
        display = Display("eno2", WIDTH, HEIGHT, brightness=255, firmware=11, color_order="BGR")
    with display as d:
        try:
            DEMOS[name](d)
        finally:
            d.clear()
            d.show()
