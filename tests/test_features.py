import http.client
import json
import socket

import numpy as np
import pytest
from PIL import Image

from ledpanels import access, engine, media, sender, web
from ledpanels.sim import SimDisplay

HDR = {"X-Requested-With": "ledpanels", "Content-Type": "application/json"}


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


@pytest.fixture
def lan_server(tmp_path):
    ip = lan_ip()
    if not ip:
        pytest.skip("no non-loopback address to simulate a guest")
    player = engine.Player(SimDisplay(32, 8), tmp_path / "media", tmp_path / "state.json")
    srv = web.serve_in_thread(player, "0.0.0.0", 0, access.Access(admin_pin="4321", guest_max_seconds=30))
    yield srv, ip
    srv.shutdown()
    srv.server_close()
    player.close()


def call(host, port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection(host, port)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data, cookie = r.read(), r.getheader("Set-Cookie")
    c.close()
    return r.status, data, cookie


def test_guest_flow(lan_server):
    srv, ip = lan_server
    port = srv.server_address[1]
    guest = lambda m, p, b=None: call(ip, port, m, p, b, HDR)
    admin = lambda m, p, b=None: call("127.0.0.1", port, m, p, b, HDR)

    assert guest("GET", "/api/status")[0] == 403  # closed by default
    assert json.loads(guest("GET", "/api/whoami")[1])["role"] is None

    assert admin("POST", "/api/guest", json.dumps({"action": "open", "minutes": 5}))[0] == 200
    assert json.loads(guest("GET", "/api/whoami")[1])["role"] == "guest"
    assert guest("GET", "/api/status")[0] == 200

    # Guests may send text (duration capped) but not sources, uploads or settings.
    assert guest("POST", "/api/play", json.dumps({"kind": "text", "text": "hi", "duration": 9999}))[0] == 200
    assert srv.player.status()["job"]["duration"] == 30
    assert guest("POST", "/api/play", json.dumps({"kind": "camera"}))[0] == 403
    assert guest("POST", "/api/play", json.dumps({"kind": "dmx"}))[0] == 403
    assert guest("POST", "/api/stop")[0] == 403
    assert guest("POST", "/api/brightness", json.dumps({"percent": 5}))[0] == 403
    assert guest("PUT", "/api/media/a.png", b"x")[0] == 403
    assert guest("GET", "/api/guest")[0] == 403

    admin("POST", "/api/guest", json.dumps({"locked": True}))
    assert guest("POST", "/api/frame", b"x")[0] == 403
    admin("POST", "/api/guest", json.dumps({"action": "close"}))
    assert guest("GET", "/api/status")[0] == 403


def test_remote_admin_login(lan_server):
    srv, ip = lan_server
    port = srv.server_address[1]
    assert call(ip, port, "POST", "/api/login", json.dumps({"pin": "nope"}), HDR)[0] == 401
    status, _, cookie = call(ip, port, "POST", "/api/login", json.dumps({"pin": "4321"}), HDR)
    assert status == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    h = {**HDR, "Cookie": cookie.split(";")[0]}
    assert call(ip, port, "POST", "/api/brightness", json.dumps({"percent": 50}), h)[0] == 200


def test_fonts_calibration_dmx_endpoints(tmp_path):
    player = engine.Player(SimDisplay(32, 8), tmp_path / "media", tmp_path / "state.json")
    srv = web.serve_in_thread(player, "127.0.0.1", 0)
    port = srv.server_address[1]
    try:
        names = json.loads(call("127.0.0.1", port, "GET", "/api/fonts", headers={"Host": f"localhost:{port}"})[1])
        assert "hershey-duplex" in names
        h = {**HDR, "Host": f"localhost:{port}"}
        assert call("127.0.0.1", port, "POST", "/api/calibration", json.dumps({"gamma": 2.2, "balance": [100, 90, 80]}), h)[0] == 200
        assert player.display.calibration == (2.2, (1.0, 0.9, 0.8))
        assert call("127.0.0.1", port, "POST", "/api/calibration", json.dumps({"gamma": 99}), h)[0] == 400
        assert call("127.0.0.1", port, "POST", "/api/dmx", json.dumps({"width": 8, "height": 4}), h)[0] == 200
        assert player.dmx_cfg["width"] == 8
    finally:
        srv.shutdown()
        srv.server_close()
        player.close()


class FakeSock:
    def __init__(self):
        self.sent = []

    def send(self, b):
        self.sent.append(bytes(b))

    def close(self):
        pass


def test_calibration_lut_and_color_order(monkeypatch):
    sock = FakeSock()
    monkeypatch.setattr(sender, "_tx_socket", lambda iface: sock)
    d = sender.Display("x", 2, 1, color_order="BGR")
    d.show(np.array([[[10, 20, 30], [255, 255, 255]]], np.uint8))
    px = [p for p in sock.sent if p[12] == 0x55][0]
    assert px[21:27] == bytes([30, 20, 10, 255, 255, 255])  # BGR on the wire

    d.set_calibration(2.0, (1.0, 0.5, 1.0))
    sock.sent.clear()
    d.show(np.array([[[255, 255, 0], [128, 128, 128]]], np.uint8))
    px = [p for p in sock.sent if p[12] == 0x55][0]
    assert px[21:24] == bytes([0, 128, 255])  # B, G at 50% balance (127.5 rounds to 128), R
    assert px[24] == round(255 * (128 / 255) ** 2)


def test_gif_plays_with_frame_delays(tmp_path):
    frames = [Image.new("RGB", (8, 4), c) for c in ((255, 0, 0), (0, 255, 0))]
    gif = tmp_path / "a.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=40, loop=0)
    d = SimDisplay(8, 4)
    seen = []
    orig = d.show
    d.show = lambda f=None: (orig(f), seen.append(d.canvas[0, 0].tolist()))
    media.play_gif(d, str(gif), loop=False)
    assert seen == [[255, 0, 0], [0, 255, 0]]
    assert media.first_frame(str(gif)).shape == (4, 8, 3)
