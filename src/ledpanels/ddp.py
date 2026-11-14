"""DDP (Distributed Display Protocol) input: external apps push full-canvas RGB frames over UDP.

Header (10 bytes): flags (0x40 = v1, | 0x01 PUSH on the last packet of a frame, | 0x10 adds a 4-byte
timecode), sequence, data type (ignored, always read as RGB), destination id (1 = display), data offset
(u32 BE, bytes) and data length (u16 BE). Data is row-major RGB over the whole canvas.
"""

import select
import socket
import struct
import threading
import time

import numpy as np

PORT = 4048
MAX_DATA = 1440  # 480 RGB pixels per packet
FLAG_PUSH = 0x01
FLAG_TIME = 0x10


def parse(data: bytes):
    """Return (offset, payload, push) for a v1 display data packet, else None."""
    if len(data) < 10 or data[0] & 0xC0 != 0x40 or data[3] != 1 or data[0] & 0x06:  # skip query/reply
        return None
    offset, length = struct.unpack(">IH", data[4:10])
    start = 14 if data[0] & FLAG_TIME else 10
    return offset, data[start : start + length], bool(data[0] & FLAG_PUSH)


def packets(frame: np.ndarray, seq: int = 1):
    """Split an (H, W, 3) RGB frame into DDP packets, PUSH set on the last."""
    flat = np.ascontiguousarray(frame, np.uint8).tobytes()
    out = []
    for off in range(0, len(flat), MAX_DATA):
        chunk = flat[off : off + MAX_DATA]
        flags = 0x40 | (FLAG_PUSH if off + MAX_DATA >= len(flat) else 0)
        out.append(struct.pack(">BBBBIH", flags, seq & 15 or 1, 0x0B, 1, off, len(chunk)) + chunk)
    return out


class DdpInput:
    """Listens on UDP; calls on_frame(frame) on each PUSH and on_timeout() once data stops for `timeout` s."""

    def __init__(self, width, height, on_frame, on_timeout, host="0.0.0.0", port=PORT, timeout=2.0):
        self.shape = (height, width, 3)
        self._buf = bytearray(width * height * 3)
        self._on_frame, self._on_timeout = on_frame, on_timeout
        self.timeout = timeout
        self.packets = self.frames = 0
        self.source = None
        self.last_data = 0.0
        self.live = False
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()
        self._sock.close()

    def _loop(self):
        while not self._stop.is_set():
            if select.select([self._sock], [], [], 0.1)[0]:
                try:
                    data, (ip, _port) = self._sock.recvfrom(1500)
                except OSError:
                    continue
                self._on_packet(data, ip)
            if self.live and time.monotonic() - self.last_data > self.timeout:
                self.live = False
                self._on_timeout()

    def _on_packet(self, data, ip):
        p = parse(data)
        if not p:
            return
        offset, payload, push = p
        n = max(0, min(len(payload), len(self._buf) - offset))
        self._buf[offset : offset + n] = payload[:n]
        self.packets += 1
        self.source, self.last_data = ip, time.monotonic()
        if push:
            self.frames += 1
            self.live = True
            self._on_frame(np.frombuffer(bytes(self._buf), np.uint8).reshape(self.shape))

    def status(self) -> dict:
        return {"port": self.port, "live": self.live, "packets": self.packets, "frames": self.frames,
                "source": self.source}


def send_test(width, height, target="127.0.0.1", port=PORT, seconds=10.0, fps=30.0):
    """Send a moving rainbow so the input can be tried without another app."""
    import cv2

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    hue_x = np.arange(width) * 180 // width
    end, seq = time.monotonic() + seconds, 1
    while time.monotonic() < end:
        hsv = np.full((height, width, 3), 255, np.uint8)
        hsv[:, :, 0] = (hue_x + int(time.monotonic() * 60)) % 180
        for pkt in packets(cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB), seq):
            sock.sendto(pkt, (target, port))
        seq = seq % 15 + 1
        time.sleep(1 / fps)
    sock.close()
