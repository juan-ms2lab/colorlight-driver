"""Web control panel (standard library server) with admin and guest roles."""

import io
import json
import re
import socket
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import cv2
import numpy as np
import segno

from . import access as acc
from . import fonts, games, patterns
from .games.pad import PAD
from .media import IMAGE_EXT, VIDEO_EXT, first_frame, fit

STATIC_DIR = Path(__file__).parent / "static"
MAX_JSON = 1 << 20
MAX_UPLOAD = 2 << 30
_SAFE_NAME = re.compile(r"[^\w.\- ]")


def clean_name(raw: str) -> str:
    """Return a safe flat filename with a supported extension, or raise ValueError."""
    name = _SAFE_NAME.sub("_", Path(unquote(raw)).name).lstrip(".")
    if Path(name).suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
        raise ValueError("unsupported file type")
    return name


def media_kind(name: str) -> str:
    return "image" if Path(name).suffix.lower() in IMAGE_EXT else "video"


class Handler(BaseHTTPRequestHandler):
    server_version = "ledpanels"

    def log_message(self, *args):
        pass

    # ---- plumbing

    def _send(self, code, body: bytes, ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200, headers=None):
        self._send(code, json.dumps(obj).encode(), headers=headers)

    def _error(self, code, msg):
        self._json({"error": msg}, code)

    def _body(self, limit: int) -> bytes:
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ValueError("Content-Length required")
        if not 0 <= n <= limit:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def _json_body(self):
        try:
            return json.loads(self._body(MAX_JSON) or b"{}")
        except json.JSONDecodeError:
            raise ValueError("invalid JSON")

    def _token(self):
        try:
            return SimpleCookie(self.headers.get("Cookie", ""))["ledsession"].value
        except Exception:
            return None

    def _auth(self, method: str):
        """Return the caller's role. The Host check stops DNS rebinding from reaching admin on loopback."""
        ip = self.client_address[0]
        if ip in acc.LOOPBACK_IPS and self.headers.get("Host", "") not in self.server.allowed_hosts:
            raise PermissionError("bad Host header")
        if method != "GET" and self.headers.get("X-Requested-With") != "ledpanels":
            raise PermissionError("missing X-Requested-With header")
        return self.server.access.role(ip, self._token())

    def _handle(self, method):
        path = urlparse(self.path).path
        try:
            self._dispatch(method, path, self._auth(method))
        except PermissionError as e:
            self._error(403, str(e))
        except FileNotFoundError:
            self._error(404, "not found")
        except ValueError as e:
            self._error(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"web error on {method} {path}: {type(e).__name__}: {e}")
            self._error(500, "internal error")

    do_GET = lambda self: self._handle("GET")
    do_POST = lambda self: self._handle("POST")
    do_PUT = lambda self: self._handle("PUT")
    do_DELETE = lambda self: self._handle("DELETE")

    # ---- routes

    def _dispatch(self, method, path, role):
        ac, player = self.server.access, self.server.player
        ip = self.client_address[0]
        if method == "GET" and path in ("/", "/index.html"):
            return self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/whoami":
            return self._json({"role": role, "guest": ac.guest_state() if role else None,
                               "via": "local" if ip in acc.LOOPBACK_IPS else "login",
                               "pin_login": bool(ac.admin_pin)})
        if method == "POST" and path == "/api/login":
            try:
                token = ac.login(ip, str(self._json_body().get("pin", "")))
            except ValueError as e:
                return self._error(429, str(e))
            if token is None:
                return self._error(401, "wrong PIN")
            cookie = f"ledsession={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={acc.SESSION_SECONDS}"
            return self._json({"ok": True}, headers={"Set-Cookie": cookie})
        if method == "POST" and path == "/api/logout":
            ac.logout(self._token())
            return self._json({"ok": True}, headers={"Set-Cookie": "ledsession=; Max-Age=0; Path=/"})
        if role is None:
            raise PermissionError("the panels are not open to guests right now")
        if self._game_route(method, path, role, ip):
            return
        if role == "guest":
            return self._guest_route(method, path)

        if method == "GET" and path == "/api/guest":
            return self._json({**ac.guest_state(), "urls": lan_urls(self.server.server_address[1])})
        if method == "GET" and path == "/api/guest/qr.svg":
            urls = lan_urls(self.server.server_address[1])
            if not urls:
                raise FileNotFoundError
            buf = io.BytesIO()
            segno.make(urls[0], error="m").save(buf, kind="svg", scale=6, border=2)
            return self._send(200, buf.getvalue(), "image/svg+xml")
        if method == "POST" and path == "/api/guest":
            b = self._json_body()
            if "locked" in b:
                ac.locked = bool(b["locked"])
            if b.get("action") == "open":
                ac.open_guests(b.get("minutes"))
            elif b.get("action") == "close":
                ac.close_guests()
            return self._json(ac.guest_state())
        if method == "POST" and path == "/api/calibration":
            b = self._json_body()
            player.set_calibration(b.get("gamma", 1.0), b.get("balance", [100, 100, 100]))
            return self._json({"ok": True})
        if method == "POST" and path == "/api/dmx":
            player.set_dmx(self._json_body())
            return self._json({"ok": True})
        return self._route(method, path)

    def _game_route(self, method, path, role, ip) -> bool:
        """Game catalog and controller endpoints, open to guests and admins. Returns True if handled."""
        ac = self.server.access
        if method == "GET" and path == "/api/games":
            self._json(games.catalog())
            return True
        if method == "POST" and path in ("/api/game/join", "/api/game/input", "/api/game/leave",
                                         "/api/game/message", "/api/game/state"):
            if role == "guest" and ac.locked:
                raise PermissionError("guest control is paused by the host")
            if not ac.limiter.allow((ip, "game"), 60, 90):
                self._error(429, "slow down")
                return True
            body = self._json_body()
            token = str(body.get("token", ""))
            if path == "/api/game/join":
                slot, token = PAD.join(ip if role == "guest" else None)
                self._json({"slot": slot, "token": token, "game": games.status()})
            elif path == "/api/game/input":
                PAD.set_keys(token, body.get("keys"))
                self._json({"ok": True})
            elif path == "/api/game/message":
                data = body.get("data")
                if len(json.dumps(data)) > 40000:
                    raise ValueError("message too large")
                PAD.put_message(token, data)
                self._json({"ok": True})
            elif path == "/api/game/state":
                self._json(games.private_state(token))
            else:
                PAD.leave(token)
                self._json({"ok": True})
            return True
        return False

    def _guest_route(self, method, path):
        ac, player = self.server.access, self.server.player
        ip = self.client_address[0]
        if method == "GET" and (path in ("/api/status", "/api/patterns", "/api/fonts", "/api/media", "/api/preview.png")
                                or re.fullmatch(r"/api/media/[^/]+/thumb", path)):
            return self._route(method, path)
        if method == "POST" and path in ("/api/play", "/api/frame"):
            if ac.locked:
                raise PermissionError("guest control is paused by the host")
            job = self._guest_job(self._json_body()) if path == "/api/play" else None
            rate = (0.5, 2) if path == "/api/play" else (15, 15)
            if not ac.limiter.allow((ip, path), *rate):
                return self._error(429, "slow down")
            if path == "/api/frame":
                return self._frame()
            player.play(job)
            return self._json({"ok": True})
        raise PermissionError("admins only")

    def _guest_job(self, job):
        if not isinstance(job, dict) or job.get("kind") not in acc.GUEST_KINDS:
            raise PermissionError("guests cannot start that")
        if job["kind"] == "text" and len(str(job.get("text", ""))) > acc.GUEST_TEXT_MAX:
            raise ValueError(f"text is limited to {acc.GUEST_TEXT_MAX} characters")
        allowed = ("kind", "name", "text", "color", "speed", "fit", "loop", "fmt", "font", "size")
        out = {k: job[k] for k in allowed if k in job}
        cap = self.server.access.guest_max_seconds
        d = job.get("duration")
        out["duration"] = min(d, cap) if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0 else cap
        if job["kind"] == "game":
            out.pop("duration")  # games end on their own after a stretch with no button presses
        return out

    def _route(self, method, path):
        player = self.server.player
        if method == "GET":
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/status":
                return self._json(player.status())
            if path == "/api/patterns":
                return self._json(patterns.NAMES)
            if path == "/api/fonts":
                return self._json(fonts.names())
            if path == "/api/media":
                return self._json(self._list_media())
            if path == "/api/preview.png":
                return self._send(200, self._preview(), "image/png")
            m = re.fullmatch(r"/api/media/([^/]+)/thumb", path)
            if m:
                return self._send(200, self._thumb(unquote(m.group(1))), "image/png")
        elif method == "POST":
            if path == "/api/play":
                player.play(self._json_body())
                return self._json({"ok": True})
            if path == "/api/stop":
                player.stop()
                return self._json({"ok": True})
            if path == "/api/frame":
                return self._frame()
            if path == "/api/brightness":
                player.set_brightness(int(self._json_body()["percent"]))
                return self._json({"ok": True})
            if path == "/api/schedule":
                b = self._json_body()
                player.set_schedule(b.get("enabled", False), b.get("on", ""), b.get("off", ""))
                return self._json({"ok": True})
            if path == "/api/playlist":
                b = self._json_body()
                player.set_playlist(b.get("items", []), b.get("loop", True))
                return self._json({"ok": True})
            if path == "/api/autoplay":
                player.set_autoplay(bool(self._json_body().get("enabled")))
                return self._json({"ok": True})
        elif method == "PUT":
            m = re.fullmatch(r"/api/media/([^/]+)", path)
            if m:
                return self._upload(clean_name(m.group(1)))
        elif method == "DELETE":
            m = re.fullmatch(r"/api/media/([^/]+)", path)
            if m:
                (self.server.player.media_dir / clean_name(m.group(1))).unlink()
                return self._json({"ok": True})
        raise FileNotFoundError

    def _list_media(self):
        out = []
        for p in sorted(self.server.player.media_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT:
                out.append({"name": p.name, "kind": media_kind(p.name), "size": p.stat().st_size})
        return out

    def _upload(self, name):
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ValueError("Content-Length required")
        if not 0 < n <= MAX_UPLOAD:
            raise ValueError("bad upload size")
        dest = self.server.player.media_dir / name
        tmp = dest.with_name(dest.name + ".part")
        try:
            with open(tmp, "wb") as f:
                left = n
                while left:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        raise ValueError("upload interrupted")
                    f.write(chunk)
                    left -= len(chunk)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        self._json({"ok": True, "name": name})

    def _frame(self):
        d = self.server.player.display
        body = self._body(d.width * d.height * 4)
        for channels in (4, 3):
            if len(body) == d.width * d.height * channels:
                arr = np.frombuffer(body, np.uint8).reshape(d.height, d.width, channels)[:, :, :3]
                self.server.player.paint_frame(arr)
                return self._json({"ok": True})
        raise ValueError(f"frame must be {d.width}x{d.height} RGB or RGBA bytes")

    def _preview(self) -> bytes:
        rgb = self.server.player.display.canvas
        ok, png = cv2.imencode(".png", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        return png.tobytes()

    def _thumb(self, name) -> bytes:
        p = self.server.player.media_dir / clean_name(name)
        if not p.is_file():
            raise FileNotFoundError
        key = (p.name, p.stat().st_mtime_ns)
        cache = self.server.thumbs
        if key not in cache:
            rgb = first_frame(str(p))
            d = self.server.player.display
            small = fit(rgb, d.width // 2, d.height // 2)
            cache[key] = cv2.imencode(".png", cv2.cvtColor(small, cv2.COLOR_RGB2BGR))[1].tobytes()
        return cache[key]


def lan_urls(port: int) -> list[str]:
    """Addresses other devices on the network can use (no traffic is sent to find them)."""
    urls = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            urls.append(f"http://{s.getsockname()[0]}:{port}/")
    except OSError:
        pass
    return urls


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, player, host: str, port: int, access: acc.Access | None = None):
        super().__init__((host, port), Handler)
        self.player = player
        self.access = access or acc.Access()
        self.thumbs = {}
        p = self.server_address[1]
        self.allowed_hosts = {f"localhost:{p}", f"127.0.0.1:{p}", f"[::1]:{p}"}

    def handle_error(self, request, client_address):
        # A phone that drops off the Wi-Fi mid-request is routine, not worth a traceback.
        if isinstance(sys.exception(), (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)

def serve_in_thread(player, host="127.0.0.1", port=8080, access=None) -> Server:
    server = Server(player, host, port, access)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
