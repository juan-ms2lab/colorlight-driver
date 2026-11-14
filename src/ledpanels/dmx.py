"""sACN (E1.31) and Art-Net input, mapped onto a virtual pixel grid and scaled to the display."""

import select
import socket
import struct
import threading
import time

import cv2
import numpy as np

from .timing import pause, running

SACN_PORT = 5568
ARTNET_PORT = 6454
CHANNELS_PER_UNIVERSE = 510  # 170 RGB pixels; the last 2 of 512 slots stay unused
SOURCE_TIMEOUT = 2.5
MAX_SOURCES = 64

DEFAULT_CONFIG = {
    "protocols": ["sacn", "artnet"],
    "width": 96,
    "height": 16,
    "start_universe": 1,
    "layout": "linear",     # linear: pixels run on through consecutive universes; per_row: one universe per row
    "order": "RGB",
    "serpentine": False,
    "scale": "stretch",     # stretch or contain
    "timeout": 5,           # seconds without data before the panels go black (0 = hold last frame)
    "interface_ip": "0.0.0.0",
}
_ACN_ID = b"ASC-E1.17\x00\x00\x00"


def validate_config(cfg) -> dict:
    """Return a complete, checked config or raise ValueError."""
    if not isinstance(cfg, dict):
        raise ValueError("dmx config must be an object")
    c = {**DEFAULT_CONFIG, **cfg}
    protos = c["protocols"]
    if not (isinstance(protos, list) and protos and set(protos) <= {"sacn", "artnet"}):
        raise ValueError("protocols must be a non-empty list of sacn and/or artnet")
    for key, lo, hi in (("width", 1, 512), ("height", 1, 512), ("start_universe", 0, 63990), ("timeout", 0, 3600)):
        v = c[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ValueError(f"{key} must be {lo}-{hi}")
    if c["layout"] not in ("linear", "per_row"):
        raise ValueError("layout must be linear or per_row")
    if c["layout"] == "per_row" and c["width"] > 170:
        raise ValueError("per_row layout needs width of at most 170 pixels")
    if not isinstance(c["order"], str) or sorted(c["order"].upper()) != ["B", "G", "R"]:
        raise ValueError("order must be a permutation of RGB")
    c["order"] = c["order"].upper()
    if c["scale"] not in ("stretch", "contain"):
        raise ValueError("scale must be stretch or contain")
    try:
        socket.inet_aton(c["interface_ip"])
    except (OSError, TypeError):
        raise ValueError("interface_ip must be an IPv4 address")
    c["serpentine"] = bool(c["serpentine"])
    return c


def universe_count(cfg) -> int:
    if cfg["layout"] == "per_row":
        return cfg["height"]
    return -(-cfg["width"] * cfg["height"] * 3 // CHANNELS_PER_UNIVERSE)


def parse_sacn(data: bytes):
    """Return (universe, dmx bytes, priority, cid hex, source name) or None."""
    if len(data) < 126 or data[4:16] != _ACN_ID:
        return None
    if struct.unpack(">I", data[18:22])[0] != 4 or struct.unpack(">I", data[40:44])[0] != 2:
        return None
    if data[117] != 2 or data[125] != 0:  # DMP set-property, DMX start code 0
        return None
    if data[112] & 0x40:  # stream terminated
        return None
    count = struct.unpack(">H", data[123:125])[0] - 1
    universe = struct.unpack(">H", data[113:115])[0]
    name = data[44:108].split(b"\0")[0].decode("utf-8", "replace")
    return universe, data[126 : 126 + max(0, count)], data[108], data[22:38].hex(), name


def parse_artnet(data: bytes):
    """Return (universe, dmx bytes) for an ArtDMX packet, else None."""
    if len(data) < 18 or data[:8] != b"Art-Net\x00" or struct.unpack("<H", data[8:10])[0] != 0x5000:
        return None
    universe = (data[15] << 8) | data[14]
    length = struct.unpack(">H", data[16:18])[0]
    return universe, data[18 : 18 + length]


def scale_nearest(grid: np.ndarray, width: int, height: int, mode: str) -> np.ndarray:
    gh, gw = grid.shape[:2]
    if mode == "stretch":
        return cv2.resize(grid, (width, height), interpolation=cv2.INTER_NEAREST)
    s = min(width / gw, height / gh)
    nw, nh = max(1, round(gw * s)), max(1, round(gh * s))
    out = np.zeros((height, width, 3), np.uint8)
    x0, y0 = (width - nw) // 2, (height - nh) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = cv2.resize(grid, (nw, nh), interpolation=cv2.INTER_NEAREST)
    return out


class DmxInput:
    def __init__(self, cfg):
        self.cfg = validate_config(cfg)
        self._flat = np.zeros(self.cfg["width"] * self.cfg["height"] * 3, np.uint8)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._arb: dict[int, dict[str, tuple[int, float]]] = {}
        self.sources: dict[str, dict] = {}
        self.version = 0
        self.packets = 0
        self.last_data = 0.0
        self.warning = None
        self._socks: list[socket.socket] = []
        self._thread = None

    # ---- lifecycle

    def start(self):
        try:
            if "sacn" in self.cfg["protocols"]:
                self._socks.append(self._open_sacn())
            if "artnet" in self.cfg["protocols"]:
                self._socks.append(self._udp(ARTNET_PORT))
        except OSError as e:
            self.stop()
            raise ValueError(f"cannot listen for DMX data: {e}")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join()
        for s in self._socks:
            s.close()
        self._socks = []

    def _udp(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        s.setblocking(False)
        return s

    def _open_sacn(self):
        s = self._udp(SACN_PORT)
        start, n = self.cfg["start_universe"], universe_count(self.cfg)
        joined = 0
        for u in range(start, start + n):
            group = socket.inet_aton(f"239.255.{u >> 8}.{u & 255}")
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             group + socket.inet_aton(self.cfg["interface_ip"]))
                joined += 1
            except OSError:
                break
        if joined < n:
            self.warning = (f"Joined {joined} of {n} multicast groups (system limit). "
                            "Use unicast to this computer or raise net.ipv4.igmp_max_memberships.")
        return s

    # ---- receive

    def _loop(self):
        while not self._stop.is_set():
            ready, _, _ = select.select(self._socks, [], [], 0.25)
            for s in ready:
                try:
                    data, (ip, _port) = s.recvfrom(1500)
                except OSError:
                    continue
                if s.getsockname()[1] == SACN_PORT:
                    self._on_sacn(data, ip)
                else:
                    self._on_artnet(data, ip)

    def _on_sacn(self, data, ip):
        p = parse_sacn(data)
        if not p:
            return
        universe, dmx, priority, cid, name = p
        now = time.monotonic()
        with self._lock:
            srcs = self._arb.setdefault(universe, {})
            for k in [k for k, (_, t) in srcs.items() if now - t > SOURCE_TIMEOUT]:
                del srcs[k]
            top = max((pr for k, (pr, _) in srcs.items() if k != cid), default=-1)
            if priority < top:
                return
            srcs[cid] = (priority, now)
            self._apply(universe, dmx, now, f"sacn:{ip}", name or "sACN source")

    def _on_artnet(self, data, ip):
        p = parse_artnet(data)
        if p:
            with self._lock:
                self._apply(p[0], p[1], time.monotonic(), f"artnet:{ip}", "Art-Net source")

    def _apply(self, universe, dmx, now, source, name):
        c = self.cfg
        idx = universe - c["start_universe"]
        self.packets += 1
        info = self.sources.setdefault(source, {"name": name, "universes": set()})
        info["last"] = now
        if len(info["universes"]) < 1024:
            info["universes"].add(universe)
        if len(self.sources) > MAX_SOURCES:
            del self.sources[min(self.sources, key=lambda k: self.sources[k]["last"])]
        if idx < 0:
            return
        if c["layout"] == "linear":
            base, width = idx * CHANNELS_PER_UNIVERSE, CHANNELS_PER_UNIVERSE
        else:
            if idx >= c["height"]:
                return
            base, width = idx * c["width"] * 3, c["width"] * 3
        n = min(len(dmx), width, self._flat.size - base)
        if n <= 0:
            return
        self._flat[base : base + n] = np.frombuffer(dmx[:n], np.uint8)
        self.version += 1
        self.last_data = now

    # ---- output

    def grid(self) -> np.ndarray:
        c = self.cfg
        with self._lock:
            g = self._flat.reshape(c["height"], c["width"], 3).copy()
        if c["serpentine"]:
            g[1::2] = g[1::2, ::-1]
        return g[:, :, [c["order"].index(ch) for ch in "RGB"]]

    def timed_out(self) -> bool:
        t = self.cfg["timeout"]
        return bool(t) and self.last_data > 0 and time.monotonic() - self.last_data > t

    def status(self) -> dict:
        now = time.monotonic()
        with self._lock:
            live = [{"source": k, "name": v["name"], "universes": len(v["universes"])}
                    for k, v in self.sources.items() if now - v["last"] < 5]
        return {"packets": self.packets, "sources": live, "warning": self.warning,
                "universes_needed": universe_count(self.cfg), "waiting": self.last_data == 0.0}


_active: DmxInput | None = None


def status():
    a = _active
    return a.status() if a else None


def run(display, cfg, stop=None, deadline=None):
    """Display incoming DMX data until stopped. Blank until the first packet arrives."""
    global _active
    inp = DmxInput(cfg)
    inp.start()
    _active = inp
    try:
        display.clear()
        display.show()
        seen, last_sent, black = 0, 0.0, True
        while running(stop, deadline):
            now = time.monotonic()
            if inp.timed_out():
                if not black:
                    display.clear()
                    display.show()
                    black = True
            elif inp.version != seen or (not black and now - last_sent > 0.25):
                seen = inp.version
                black = False
                display.show(scale_nearest(inp.grid(), display.width, display.height, inp.cfg["scale"]))
                last_sent = now
            pause(stop, 1 / 40)
    finally:
        _active = None
        inp.stop()
