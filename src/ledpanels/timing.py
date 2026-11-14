"""Helpers for loops that must stop on request or at a deadline."""

import threading
import time


def running(stop: threading.Event | None, deadline: float | None = None) -> bool:
    if stop is not None and stop.is_set():
        return False
    return deadline is None or time.monotonic() < deadline


def pause(stop: threading.Event | None, seconds: float):
    if stop is None:
        time.sleep(seconds)
    else:
        stop.wait(seconds)


def hold(show, stop=None, deadline=None, interval: float = 0.25):
    """Resend the current frame so a dropped packet recovers."""
    while running(stop, deadline):
        show()
        pause(stop, interval)
