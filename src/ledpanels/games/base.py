"""Helpers shared by multiplayer games: lobby countdown, text drawing and player colors."""

import math

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX
PLAYER_COLORS = [(0, 200, 255), (255, 140, 0), (120, 255, 60), (255, 60, 200)]


class Lobby:
    """Counts down once someone has joined, so friends have time to join before a round starts."""

    def __init__(self, seconds: float = 8.0):
        self.seconds = seconds
        self.left: float | None = None

    def reset(self):
        self.left = None

    def update(self, dt: float, joined) -> bool:
        """Return True when the countdown reaches zero with at least one player."""
        if not joined:
            self.left = None
            return False
        if self.left is None:
            self.left = self.seconds
        self.left -= dt
        return self.left <= 0

    @property
    def count(self) -> int | None:
        return None if self.left is None else max(0, math.ceil(self.left))


def text(canvas: np.ndarray, s: str, x: int, y: int, scale: float = 0.45, color=(255, 255, 255), thickness: int = 1):
    cv2.putText(canvas, s, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


def center_text(canvas: np.ndarray, s: str, y: int, scale: float = 0.8, color=(255, 255, 255),
                thickness: int = 1, box: bool = True):
    (tw, th), _ = cv2.getTextSize(s, FONT, scale, thickness)
    x = (canvas.shape[1] - tw) // 2
    if box:
        cv2.rectangle(canvas, (x - 4, y - th - 4), (x + tw + 4, y + 5), (0, 0, 0), -1)
    cv2.putText(canvas, s, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)
