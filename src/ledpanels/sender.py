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
        for pkt in proto.pixel_packets(out):
            send(pkt)
        time.sleep(self._sync_gap)
        for _ in range(self._repeat):
            send(self._sync_pkt)
        end = time.perf_counter()
        self.send_ms = 0.8 * self.send_ms + 0.2 * (end - start) * 1000
        if self._last_start is not None:
            self.fps = 0.8 * self.fps + 0.2 / max(1e-6, start - self._last_start)
        self._last_start = start
