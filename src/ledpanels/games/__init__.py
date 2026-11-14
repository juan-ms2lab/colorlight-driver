"""Games played from phone controllers. Each game is a class with update(dt, pads) and render(canvas)."""

import time

from ..timing import pause, running
from .chomp import Chomp
from .flap import Flap
from .pad import PAD
from .platformer import Platformer
from .pong import Pong

STEP = 1 / 60
IDLE_SECONDS = 120  # a game with no button presses for this long ends and the panels blank

REGISTRY = {g.id: g for g in (Platformer, Flap, Chomp, Pong)}


def catalog() -> list[dict]:
    return [{"id": g.id, "title": g.title, "description": g.description,
             "max_players": g.max_players, "controls": g.controls} for g in REGISTRY.values()]


def status():
    s = PAD.status()
    if s:
        s["title"] = REGISTRY[s["id"]].title
    return s


def run(display, name: str, stop=None, deadline=None):
    game = REGISTRY[name](display.width, display.height)
    PAD.start(name, game.max_players)
    try:
        last, acc = time.monotonic(), 0.0
        while running(stop, deadline):
            now = time.monotonic()
            acc = min(acc + now - last, 0.25)
            last = now
            while acc >= STEP:
                game.update(STEP, PAD.snapshot())
                acc -= STEP
            game.render(display.canvas)
            display.show()
            if PAD.idle_seconds() > IDLE_SECONDS:
                return
            pause(stop, max(0.0, STEP - (time.monotonic() - now)))
    finally:
        PAD.stop()
