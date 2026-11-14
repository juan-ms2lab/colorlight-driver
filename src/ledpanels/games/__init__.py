"""Games played from phone controllers. Each game is a class with update(dt, pads) and render(canvas)."""

import time

from ..timing import pause, running
from .bomber import Bombers
from .chomp import Chomp
from .flap import Flap
from .hop import Hop
from .pad import PAD
from .platformer import Platformer
from .pong import Pong
from .sketch import Sketch
from .sweep import Sweep
from .trivia import Trivia
from .tug import Tug

STEP = 1 / 60
IDLE_SECONDS = 120  # a game with no button presses for this long ends and the panels blank

REGISTRY = {g.id: g for g in (Platformer, Hop, Flap, Chomp, Bombers, Sweep, Trivia, Sketch, Tug, Pong)}
_current = None


def catalog() -> list[dict]:
    return [{"id": g.id, "title": g.title, "description": g.description, "max_players": g.max_players,
             "controls": g.controls, "layout": getattr(g, "layout", "dpad")} for g in REGISTRY.values()]


def status():
    s = PAD.status()
    if s:
        s["title"] = REGISTRY[s["id"]].title
        game = _current
        if game is not None and hasattr(game, "info"):
            s.update(game.info())  # e.g. {"state": "lobby", "host": 0}
    return s


def private_state(token: str) -> dict:
    """Public game info plus whatever only this player may see (the word to draw, their own feedback)."""
    slot = PAD.slot_of(token)
    out = dict(status() or {})
    out["slot"] = slot
    game = _current
    if game is not None and hasattr(game, "private_info"):
        out.update(game.private_info(slot))
    return out


def run(display, name: str, stop=None, deadline=None):
    global _current
    game = REGISTRY[name](display.width, display.height)
    PAD.start(name, game.max_players)
    _current = game
    try:
        last, acc = time.monotonic(), 0.0
        while running(stop, deadline):
            now = time.monotonic()
            acc = min(acc + now - last, 0.25)
            last = now
            if hasattr(game, "on_message"):
                for slot, data in PAD.drain():
                    game.on_message(slot, data)
            while acc >= STEP:
                game.update(STEP, PAD.snapshot())
                acc -= STEP
            game.render(display.canvas)
            display.show()
            if PAD.idle_seconds() > IDLE_SECONDS:
                return
            pause(stop, max(0.0, STEP - (time.monotonic() - now)))
    finally:
        _current = None
        PAD.stop()
