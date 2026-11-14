import http.client
import json

import cv2
import numpy as np
import pytest

from ledpanels import engine, web
from ledpanels.sim import SimDisplay

HDR = {"X-Requested-With": "ledpanels"}


@pytest.fixture
def server(tmp_path):
    player = engine.Player(SimDisplay(32, 8), tmp_path / "media", tmp_path / "state.json")
    srv = web.serve_in_thread(player, "127.0.0.1", 0)
    yield srv
    srv.shutdown()
    srv.server_close()
    player.close()


def req(srv, method, path, body=None, headers=None, host=None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port)
    h = dict(headers or {})
    h["Host"] = host or f"127.0.0.1:{port}"
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def png_bytes():
    ok, buf = cv2.imencode(".png", np.full((4, 4, 3), 200, np.uint8))
    return buf.tobytes()


def test_index_served(server):
    status, body = req(server, "GET", "/")
    assert status == 200 and b"LED Panels" in body


def test_rejects_foreign_host(server):
    status, _ = req(server, "GET", "/api/status", host="evil.example:80")
    assert status == 403


def test_rejects_writes_without_header(server):
    status, _ = req(server, "POST", "/api/stop")
    assert status == 403


def test_upload_list_thumb_delete(server):
    status, _ = req(server, "PUT", "/api/media/pic.png", png_bytes(), HDR)
    assert status == 200
    _, body = req(server, "GET", "/api/media")
    assert [m["name"] for m in json.loads(body)] == ["pic.png"]
    status, thumb = req(server, "GET", "/api/media/pic.png/thumb")
    assert status == 200 and thumb.startswith(b"\x89PNG")
    assert req(server, "DELETE", "/api/media/pic.png", headers=HDR)[0] == 200
    assert json.loads(req(server, "GET", "/api/media")[1]) == []


def test_upload_rejects_bad_type_and_sanitizes_path(server, tmp_path):
    assert req(server, "PUT", "/api/media/run.sh", b"x", HDR)[0] == 400
    assert req(server, "PUT", "/api/media/..%2F..%2Fevil.png", png_bytes(), HDR)[0] == 200
    assert not (tmp_path / "evil.png").exists()
    assert (tmp_path / "media" / "evil.png").exists()


def test_play_and_frame(server):
    body = json.dumps({"kind": "fill", "color": [1, 2, 3]})
    assert req(server, "POST", "/api/play", body, {**HDR, "Content-Type": "application/json"})[0] == 200
    frame = np.full((8, 32, 4), 255, np.uint8).tobytes()
    assert req(server, "POST", "/api/frame", frame, HDR)[0] == 200
    assert req(server, "POST", "/api/frame", b"short", HDR)[0] == 400
    status, png = req(server, "GET", "/api/preview.png")
    assert status == 200 and png.startswith(b"\x89PNG")


def test_play_invalid_job_is_400(server):
    assert req(server, "POST", "/api/play", b'{"kind":"bogus"}', HDR)[0] == 400
    assert req(server, "POST", "/api/play", b"not json", HDR)[0] == 400
