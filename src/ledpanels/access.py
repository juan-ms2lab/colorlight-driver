"""Who may do what: admin (this computer, or PIN login), guests (temporary, limited), or nobody."""

import hmac
import secrets
import threading
import time

LOOPBACK_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
SESSION_SECONDS = 12 * 3600
LOCKOUT_SECONDS = 60
MAX_FAILURES = 5

# Job kinds a guest may start; sources like the camera or DMX stay admin-only.
GUEST_KINDS = {"image", "video", "text", "clock", "pattern", "fill", "game"}
GUEST_TEXT_MAX = 60


class RateLimiter:
    """Token buckets keyed by (client, action)."""

    def __init__(self):
        self._buckets: dict[tuple, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: tuple, per_second: float, burst: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (burst, now))
            tokens = min(burst, tokens + (now - last) * per_second)
            ok = tokens >= 1
            self._buckets[key] = (tokens - 1 if ok else tokens, now)
            if len(self._buckets) > 5000:
                cutoff = now - 600
                self._buckets = {k: v for k, v in self._buckets.items() if v[1] > cutoff}
            return ok


class Access:
    def __init__(self, admin_pin: str = "", guest_max_seconds: int = 300):
        self.admin_pin = admin_pin or ""
        self.guest_max_seconds = guest_max_seconds
        self.limiter = RateLimiter()
        self._lock = threading.Lock()
        self._open = False
        self._until: float | None = None
        self.locked = False
        self._sessions: dict[str, float] = {}
        self._fails: dict[str, tuple[int, float]] = {}

    # ---- guests

    def guests_open(self) -> bool:
        with self._lock:
            return self._guests_open()

    def _guests_open(self) -> bool:
        if not self._open:
            return False
        if self._until is not None and time.monotonic() >= self._until:
            self._open = False
            return False
        return True

    def open_guests(self, minutes: float | None):
        """Open guest access for `minutes`, or until closed if None."""
        if minutes is not None and not 0 < minutes <= 7 * 24 * 60:
            raise ValueError("minutes must be between 0 and 10080")
        with self._lock:
            self._open = True
            self._until = None if minutes is None else time.monotonic() + minutes * 60

    def close_guests(self):
        with self._lock:
            self._open = False

    def guest_state(self) -> dict:
        with self._lock:
            is_open = self._guests_open()
            remaining = None
            if is_open and self._until is not None:
                remaining = max(0, round(self._until - time.monotonic()))
            return {"open": is_open, "remaining": remaining, "locked": self.locked,
                    "max_seconds": self.guest_max_seconds}

    # ---- admin sessions

    def login(self, ip: str, pin: str) -> str | None:
        """Return a session token for a correct PIN, None for a wrong one; ValueError when locked out."""
        now = time.monotonic()
        with self._lock:
            count, until = self._fails.get(ip, (0, 0.0))
            if until > now:
                raise ValueError("too many attempts, try again in a minute")
            ok = bool(self.admin_pin) and hmac.compare_digest(pin.encode(), self.admin_pin.encode())
            if not ok:
                count += 1
                self._fails[ip] = (0, now + LOCKOUT_SECONDS) if count >= MAX_FAILURES else (count, 0.0)
                if len(self._fails) > 1000:
                    self._fails = {k: v for k, v in self._fails.items() if v[1] > now}
                return None
            self._fails.pop(ip, None)
            token = secrets.token_urlsafe(32)
            self._sessions = {t: e for t, e in self._sessions.items() if e > now}
            self._sessions[token] = now + SESSION_SECONDS
            return token

    def logout(self, token: str | None):
        with self._lock:
            self._sessions.pop(token or "", None)

    def _is_session(self, token: str | None) -> bool:
        with self._lock:
            exp = self._sessions.get(token or "")
            return exp is not None and exp > time.monotonic()

    def role(self, ip: str, token: str | None) -> str | None:
        if ip in LOOPBACK_IPS or self._is_session(token):
            return "admin"
        return "guest" if self.guests_open() else None
