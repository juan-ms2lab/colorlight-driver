"""Raw-socket sender. Needs CAP_NET_RAW (root or setcap)."""

import socket
import time

import numpy as np

from . import protocol as proto


def _tx_socket(interface: str) -> socket.socket:
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    s.bind((interface, 0))
    return s


def discover(interface: str, timeout: float = 0.3, max_receivers: int = 32):
    """Query receiver cards in chain order; returns a list of Receiver."""
    rx = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(proto.ETH_P_REPLY))
    rx.bind((interface, proto.ETH_P_REPLY))
    rx.settimeout(timeout)
    found = []
    with _tx_socket(interface) as tx, rx:
        for index in range(max_receivers):
            replies = []
            for _ in range(3 if index == 0 else 1):
                tx.send(proto.build_discover(index))
                replies = _collect(rx)
                if replies:
                    break
            if not replies:
                break
            found.extend(replies)
    return found


def _collect(rx: socket.socket):
    replies = []
    while True:
        try:
            r = proto.parse_reply(rx.recv(2048))
        except socket.timeout:
            return replies
        if r:
            replies.append(r)


def build_map(displays, width: int, height: int):
    """Precompute where each canvas pixel goes in card space.

    `displays` is a list of {"canvas": [x, y, w, h], "card": [cx, cy]} with optional `flip_x`, `flip_y`,
    `reverse_panels` and `panel_width`. Returns (index, rows): `index` gathers the wire pixels from the
    flattened canvas plus one trailing black pixel (index width * height, used for gaps), and `rows` lists
    (card_y, card_x0, start, count) into it. Displays sharing a card row are merged into one contiguous run
    from the leftmost x, because chained cards only showed the last display when they were sent apart.
    """
    blank = width * height
    segments: dict[int, list] = {}
    for n, d in enumerate(displays):
        try:
            x, y, w, h = (int(v) for v in d["canvas"])
            cx, cy = (int(v) for v in d["card"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"display {n + 1} needs canvas = [x, y, w, h] and card = [x, y]")
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise ValueError(f"display {n + 1}: canvas region must lie inside the {width}x{height} canvas")
        if cx < 0 or cy < 0 or cx + w > 0xFFFF or cy + h > 0xFFFF:
            raise ValueError(f"display {n + 1}: card position out of range")
        cols = np.arange(x, x + w)
        if d.get("reverse_panels"):
            pw = int(d.get("panel_width", 128))
            if pw <= 0 or w % pw:
                raise ValueError(f"display {n + 1}: width must be a multiple of panel_width")
            cols = cols.reshape(-1, pw)[::-1].reshape(-1)
        if d.get("flip_x"):
            cols = cols[::-1]
        for r in range(h):
            src = y + (h - 1 - r if d.get("flip_y") else r)
            segments.setdefault(cy + r, []).append((cx, src * width + cols))
    index, rows, start = [], [], 0
    for cy in sorted(segments):
        segs = segments[cy]
        x0 = min(cx for cx, _ in segs)
        run = np.full(max(cx + len(c) for cx, c in segs) - x0, blank)
        for cx, c in segs:
            run[cx - x0 : cx - x0 + len(c)] = c
        index.append(run)
        rows.append((cy, x0, start, len(run)))
        start += len(run)
    return np.concatenate(index), rows


class Display:
    """Full-canvas RGB display. Draw into `canvas`, then call `show()`."""

    def __init__(
        self,
        interface: str,
        width: int,
        height: int,
        brightness: int = 255,
        firmware: int = 0,
        color_order: str = "RGB",
        sync_gap: float = 0.001,
        flip_x: bool = False,
        flip_y: bool = False,
        displays=None,
    ):
        self._flip_x = flip_x
        self._flip_y = flip_y
        self._interface = interface
        self._lut = None
        self.fps = 0.0
        self.send_ms = 0.0
        self._last_start = None
        self.width = width
        self.height = height
        self.canvas = np.zeros((height, width, 3), np.uint8)
        # Canvas regions of the physical displays (for test patterns); no entries = whole canvas at card (0, 0).
        self._map = build_map(displays, width, height) if displays else None
        self.regions = [tuple(d["canvas"]) for d in displays] if displays else [(0, 0, width, height)]
        self._order = ["RGB".index(c) for c in color_order.upper()]
        self._repeat = 2 if firmware >= 13 else 1  # v13+ wants brightness/sync twice
        self._sync_gap = sync_gap
        self._sock = _tx_socket(interface)
        self.set_brightness(brightness)

    def set_brightness(self, level: int):
        """Brightness 0-255, applied from the next frame."""
        level = max(0, min(255, int(level)))
        self._brightness_pkt = proto.build_brightness(level)
        self._sync_pkt = proto.build_sync(level)

    def close(self):
        self._sock.close()

    def reconnect(self):
        """Reopen the socket after the interface went away and came back."""
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = _tx_socket(self._interface)

    def set_calibration(self, gamma: float = 1.0, balance=(1.0, 1.0, 1.0)):
        """Per-channel output curve: out = 255 * (in/255)**gamma * balance. Identity disables it."""
        if gamma == 1 and all(b == 1 for b in balance):
            self._lut = None
            return
        x = np.arange(256) / 255
        self._lut = np.stack([np.clip(np.rint(255 * x**gamma * b), 0, 255).astype(np.uint8) for b in balance])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def clear(self):
        self.canvas[:] = 0

    def fill(self, rgb):
        self.canvas[:] = rgb

    def set_pixel(self, x: int, y: int, rgb):
        self.canvas[y, x] = rgb

    def show(self, frame: np.ndarray | None = None):
        start = time.perf_counter()
        if frame is not None:
            self.canvas[:] = frame
        view = self.canvas
        if self._flip_y:
            view = view[::-1]
        if self._flip_x:
            view = view[:, ::-1]
        if self._lut is None:
            out = np.ascontiguousarray(view[:, :, self._order])
        else:
            out = np.empty(view.shape, np.uint8)
            for i, o in enumerate(self._order):
                out[:, :, i] = self._lut[o][view[:, :, o]]
        send = self._sock.send
        for _ in range(self._repeat):
            send(self._brightness_pkt)
        if self._map is None:
            for pkt in proto.pixel_packets(out):
                send(pkt)
        else:
            index, rows = self._map
            wire = np.concatenate([out.reshape(-1, 3), np.zeros((1, 3), np.uint8)])[index]
            for y, x0, i, n in rows:
                for pkt in proto.row_packets(y, x0, wire[i : i + n]):
                    send(pkt)
        time.sleep(self._sync_gap)
        for _ in range(self._repeat):
            send(self._sync_pkt)
        end = time.perf_counter()
        self.send_ms = 0.8 * self.send_ms + 0.2 * (end - start) * 1000
        if self._last_start is not None:
            self.fps = 0.8 * self.fps + 0.2 / max(1e-6, start - self._last_start)
        self._last_start = start
