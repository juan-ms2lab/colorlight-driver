"""Command-line entry point: one-shot commands, the web control panel and setup helpers."""

import argparse
import json
import os
import re
import shlex
import signal
import sys
import threading
import time
import tomllib
import webbrowser
from pathlib import Path

import numpy as np

from . import access, animations, ddp, dmx, engine, media, patterns, timing, web
from .sender import Display, discover
from .sim import SimDisplay

DEFAULTS = {
    "interface": "eno2",
    "width": 768,
    "height": 128,
    "brightness": 100,
    "firmware": 0,
    "color_order": "BGR",
    "flip_x": False,
    "flip_y": False,
    "sync_gap": 0.001,
    "display": [],
    "host": "127.0.0.1",
    "port": 8080,
    "media_dir": "media",
    "state_file": "state.json",
    "admin_pin": "",
    "guest_max_seconds": 300,
    "ddp_port": 0,
}


def build_parser():
    p = argparse.ArgumentParser(prog="ledpanels", description="Drive Colorlight 5A-75B LED panels")
    p.add_argument("-c", "--config", default="ledpanels.toml")
    p.add_argument("-i", "--interface")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--brightness", type=int, help="percent, 0-100")
    p.add_argument("--firmware", type=int, help="receiver firmware major version, 0 = auto")
    p.add_argument("--color-order", dest="color_order")
    p.add_argument("--flip-x", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--flip-y", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--host", help="serve/app bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, help="serve/app port (default 8080)")
    p.add_argument("--simulate", action="store_true", help="no hardware: run against a simulated display")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="list receiver cards on the wire")
    sub.add_parser("clear", help="blank the panels")

    f = sub.add_parser("fill", help="fill with one color")
    f.add_argument("rgb", type=int, nargs=3, metavar=("R", "G", "B"))

    t = sub.add_parser("test", help="run a test pattern")
    t.add_argument("pattern", choices=patterns.NAMES)
    t.add_argument("--block", type=int, default=128, help="ruler block width in pixels")
    t.add_argument("--start", type=int, default=640, help="diagonal: x at the top row")
    t.add_argument("--step", type=int, default=2, help="diagonal: x pixels advanced per row")

    i = sub.add_parser("image", help="show an image")
    i.add_argument("path")
    i.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")

    v = sub.add_parser("video", help="play a video")
    v.add_argument("path")
    v.add_argument("--loop", action="store_true")
    v.add_argument("--fit", choices=["contain", "cover", "stretch"], default="contain")

    px = sub.add_parser("pixel", help="set individual pixels: X Y R G B [X Y R G B ...]")
    px.add_argument("values", type=int, nargs="+")

    tx = sub.add_parser("text", help="scroll text")
    tx.add_argument("text")
    tx.add_argument("--color", type=int, nargs=3, default=[255, 255, 255], metavar=("R", "G", "B"))
    tx.add_argument("--speed", type=float, default=80, help="pixels per second")

    ck = sub.add_parser("clock", help="show the time")
    ck.add_argument("--fmt", default="%H:%M:%S")
    ck.add_argument("--color", type=int, nargs=3, default=[255, 255, 255], metavar=("R", "G", "B"))

    sub.add_parser("serve", help="run the web control panel")
    sub.add_parser("app", help="run the web control panel and open it in the browser")
    sub.add_parser("install-service", help="write a systemd user service that runs `serve` at login")
    sub.add_parser("install-launcher", help="add an application-menu entry that runs `app`")

    bm = sub.add_parser("benchmark", help="measure how fast frames can be sent")
    bm.add_argument("--frames", type=int, default=120)

    dd = sub.add_parser("ddp-test", help="send a test rainbow over DDP to a running server (needs ddp_port)")
    dd.add_argument("--target", default="127.0.0.1", help="destination IP (default 127.0.0.1)")
    dd.add_argument("--seconds", type=float, default=10)

    dt = sub.add_parser("dmx-test", help="send a test rainbow as sACN or Art-Net (try the DMX input without other software)")
    dt.add_argument("--protocol", choices=["sacn", "artnet"], default="sacn")
    dt.add_argument("--target", help="destination IP (default: sACN multicast / Art-Net broadcast)")
    dt.add_argument("--seconds", type=float, default=20)

    su = sub.add_parser("setup", help="find which network interface the panels are on")
    su.add_argument("--write", action="store_true", help="save the interface into the config file")
    return p


def load_settings(args) -> dict:
    settings = dict(DEFAULTS)
    cfg = Path(args.config)
    if cfg.exists():
        settings.update(tomllib.loads(cfg.read_text()))
    for key in DEFAULTS:
        if getattr(args, key, None) is not None:
            settings[key] = getattr(args, key)
    settings["admin_pin"] = os.environ.get("LEDPANELS_PIN", settings["admin_pin"])
    return settings


def hold(display):
    print("Holding. Ctrl-C to stop.")
    timing.hold(display.show)


def candidate_interfaces():
    """Wired Ethernet interfaces that currently have a cable connected."""
    found = []
    for p in sorted(Path("/sys/class/net").iterdir()):
        try:
            physical = (p / "device").exists() and not (p / "wireless").exists()
            if physical and (p / "type").read_text().strip() == "1" and (p / "carrier").read_text().strip() == "1":
                found.append(p.name)
        except OSError:
            continue  # interface is down
    return found


def setup(args, s):
    names = candidate_interfaces()
    if not names:
        print("No wired interface with a cable connected. Plug in the panels and try again.")
        return 1
    hits = []
    for name in names:
        found = discover(name, timeout=0.5)
        print(f"{name}: " + (f"{len(found)} receiver(s)" if found else "no receivers"))
        if found:
            hits.append(name)
    if len(hits) != 1:
        print("Could not pick one interface automatically; set `interface` in the config.")
        return 1
    print(f'Panels found on {hits[0]}. Set interface = "{hits[0]}" in {args.config}.')
    if args.write:
        cfg = Path(args.config)
        text = cfg.read_text() if cfg.exists() else ""
        line = f'interface = "{hits[0]}"'
        text = re.sub(r"(?m)^interface\s*=.*$", line, text) if re.search(r"(?m)^interface\s*=", text) else line + "\n" + text
        cfg.write_text(text)
        print(f"Updated {cfg}")
    return 0


def dmx_test(args, s):
    base = Path(args.config).resolve().parent
    cfg = dict(dmx.DEFAULT_CONFIG)
    try:
        cfg.update(json.loads((base / s["state_file"]).read_text()).get("dmx", {}))
    except (OSError, ValueError):
        pass
    print(f"Sending {args.protocol} test pattern for {args.seconds:g}s using the saved DMX grid {cfg['width']}x{cfg['height']}, "
          f"first universe {cfg['start_universe']}. Start listening in the DMX tab first.")
    dmx.send_test(cfg, args.protocol, args.target, args.seconds)
    return 0


def benchmark(args, d):
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, d.canvas.shape, dtype=np.uint8) for _ in range(4)]
    d.show(frames[0])  # warm up
    start = time.perf_counter()
    for i in range(args.frames):
        d.show(frames[i % 4])
    secs = time.perf_counter() - start
    mbit = d.width * d.height * 3 * 8 * args.frames / secs / 1e6
    print(f"{args.frames / secs:.1f} frames/s, {secs / args.frames * 1000:.1f} ms per frame, {mbit:.0f} Mbit/s of pixel data")
    return 0


def install(args):
    base = Path(args.config).resolve().parent
    py = shlex.quote(sys.executable)
    if args.cmd == "install-service":
        dest = Path.home() / ".config/systemd/user/ledpanels.service"
        text = ("[Unit]\nDescription=LED panel controller\nAfter=network.target\n\n"
                f"[Service]\nWorkingDirectory={base}\n"
                f"ExecStart={py} -m ledpanels -c {shlex.quote(str(base / Path(args.config).name))} serve\n"
                "Restart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n")
        hint = "Enable with: systemctl --user daemon-reload && systemctl --user enable --now ledpanels"
    else:
        dest = Path.home() / ".local/share/applications/ledpanels.desktop"
        text = ("[Desktop Entry]\nType=Application\nName=LED Panels\nComment=Control the LED panels\n"
                f"Exec={py} -m ledpanels -c {shlex.quote(str(base / Path(args.config).name))} app\n"
                f"Path={base}\nTerminal=false\nCategories=Utility;\n")
        hint = "Look for 'LED Panels' in the application menu."
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    print(f"Wrote {dest}\n{hint}")
    return 0


def run(args, s):
    if args.cmd == "discover":
        receivers = discover(s["interface"])
        if not receivers:
            print("No receivers found. Check the cable, interface name and link speed.")
            return 1
        for r in receivers:
            print(f"#{r.id}: fw v{r.fw_major}.{r.fw_minor}, cabinet {r.width}x{r.height}, "
                  f"uptime {r.uptime_ms / 1000:.0f}s, packets {r.packets}")
        return 0

    if args.cmd in ("install-service", "install-launcher"):
        return install(args)
    if args.cmd == "setup":
        return setup(args, s)
    if args.cmd == "dmx-test":
        return dmx_test(args, s)
    if args.cmd == "ddp-test":
        port = s["ddp_port"] or ddp.PORT
        print(f"Sending a {s['width']}x{s['height']} DDP rainbow to {args.target}:{port} for {args.seconds:g}s.")
        ddp.send_test(s["width"], s["height"], args.target, port, args.seconds)
        return 0

    level = round(255 * max(0, min(100, s["brightness"])) / 100)
    if args.simulate:
        d = SimDisplay(s["width"], s["height"], [e["canvas"] for e in s["display"]])
    else:
        firmware = s["firmware"]
        if not firmware:
            found = discover(s["interface"])
            firmware = max((r.fw_major for r in found), default=0)
            print(f"Detected {len(found)} receiver(s), firmware major {firmware}")
        d = Display(s["interface"], s["width"], s["height"], level, firmware, s["color_order"],
                    flip_x=s["flip_x"], flip_y=s["flip_y"],
                    sync_gap=s["sync_gap"], displays=s["display"])
    with d:
        try:
            return dispatch(args, s, d)
        finally:
            # Receivers keep the last frame, so blank the panels on exit.
            d.clear()
            d.show()


def serve(args, s, d):
    base = Path(args.config).resolve().parent
    patterns.WEB_PORT = s["port"]
    player = engine.Player(d, base / s["media_dir"], base / s["state_file"])
    player.start()
    if s["ddp_port"]:
        player.ddp = ddp.DdpInput(d.width, d.height, player.paint_frame, player.end_paint,
                                  s["host"], s["ddp_port"]).start()
        print(f"DDP input on UDP {s['host']}:{s['ddp_port']}")
    server = web.Server(player, s["host"], s["port"], access.Access(s["admin_pin"], s["guest_max_seconds"]))
    url = f"http://localhost:{server.server_address[1]}/"
    print(f"Control panel: {url}  (Ctrl-C to stop)")
    if args.cmd == "app":
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    finally:
        if player.ddp:
            player.ddp.stop()
        player.close()
        server.server_close()
    return 0


def dispatch(args, s, d):
    if args.cmd == "clear":
        return 0
    if args.cmd in ("serve", "app"):
        return serve(args, s, d)
    if args.cmd == "benchmark":
        return benchmark(args, d)
    if args.cmd == "fill":
        d.fill(args.rgb)
        hold(d)
    elif args.cmd == "test":
        kw = {"ruler": {"block": args.block}, "tiles": {"size": args.block},
              "diagonal": {"start": args.start, "step": args.step},
              "ip": {"port": s["port"]}}.get(args.pattern, {})
        print("Running. Ctrl-C to stop.")
        patterns.run_pattern(args.pattern, d, **kw)
    elif args.cmd == "text":
        animations.scroll_text(d, args.text, args.color, args.speed)
    elif args.cmd == "clock":
        animations.clock(d, args.color, args.fmt)
    elif args.cmd == "image":
        d.canvas[:] = media.load_image(args.path, d.width, d.height, args.fit)
        hold(d)
    elif args.cmd == "video":
        media.play_video(d, args.path, args.loop, args.fit)
    elif args.cmd == "pixel":
        if len(args.values) % 5:
            print("pixel needs groups of 5 values: X Y R G B")
            return 2
        for k in range(0, len(args.values), 5):
            x, y, *rgb = args.values[k : k + 5]
            d.set_pixel(x, y, rgb)
        hold(d)
    return 0


def _on_sigterm(*_):
    raise KeyboardInterrupt


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)
    args = build_parser().parse_args()
    try:
        sys.exit(run(args, load_settings(args)))
    except PermissionError:
        sys.exit("Raw sockets need CAP_NET_RAW. Run with sudo or: sudo setcap cap_net_raw+ep <python binary>")
    except OSError as e:
        sys.exit(f"Network error on interface: {e}")
    except KeyboardInterrupt:
        sys.exit(0)
