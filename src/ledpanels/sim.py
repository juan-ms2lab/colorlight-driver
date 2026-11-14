"""Hardware-free stand-in for Display, for development and tests."""

import numpy as np


class SimDisplay:
    def __init__(self, width: int, height: int, regions=None):
        self.regions = [tuple(r) for r in regions] if regions else [(0, 0, width, height)]
        self.width = width
        self.height = height
        self.canvas = np.zeros((height, width, 3), np.uint8)
        self.frames = 0
        self.brightness = 255
        self.fps = 0.0
        self.send_ms = 0.0
        self.calibration = (1.0, (1.0, 1.0, 1.0))

    def set_calibration(self, gamma: float = 1.0, balance=(1.0, 1.0, 1.0)):
        self.calibration = (gamma, tuple(balance))

    def reconnect(self):
        pass

    def set_brightness(self, level: int):
        self.brightness = level

    def clear(self):
        self.canvas[:] = 0

    def fill(self, rgb):
        self.canvas[:] = rgb

    def set_pixel(self, x: int, y: int, rgb):
        self.canvas[y, x] = rgb

    def show(self, frame=None):
        if frame is not None:
            self.canvas[:] = frame
        self.frames += 1

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
