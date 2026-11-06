"""Rate limits and storage caps, so open sign-in can't fill the disk or tie up the server.

Counters live in the server's memory (the image runs one process, see the Dockerfile) and start over on
restart. With more processes each one counts on its own, so the limits multiply by their number.

Limits (each a setting, see docs/mason.md):
- API calls: MASON_RATE_API per minute per user (600), of them MASON_RATE_WRITES changes (60); signed-out
  calls MASON_RATE_ANON per minute per address (120). Reads through the GitHub proxy (/api/v1/github/) are
  not counted: one CLI command makes many, and they spend only the user's own GitHub quota.
- New accounts: MASON_SIGNUPS_PER_HOUR in all (60) and MASON_SIGNUPS_PER_IP per address (5), and no more than
  MASON_MAX_USERS accounts (1000; 0 = no cap). Logins in MASON_ALLOW_USERS always get in.
- Sign-ins in progress: at most PENDING rows each of sign-in states, device sign-ins and device codes.

Caps on what each user stores:
- MASON_MAX_KEYS active API keys (50) and at most twice that many rows with revoked or expired ones;
- MASON_MAX_SESSIONS browser sessions (20; the oldest end first);
- settings up to SETTINGS_BYTES, secrets up to 4000 characters each (one per name a plugin declares);
- audit rows: MASON_AUDIT_ROWS (retention.py).
"""

from __future__ import annotations

import threading
import time

from flask import current_app

PENDING = 5000
SETTINGS_BYTES = 32 * 1024
MAX_KEYS_TRACKED = 20000

_lock = threading.Lock()
_windows: dict[str, tuple[float, int]] = {}


class Limited(Exception):
    """A limit was hit; `retry` is seconds to wait."""

    def __init__(self, message: str, retry: int):
        super().__init__(message)
        self.retry = max(1, int(retry))


def hit(key: str, limit: int, window: int = 60) -> int:
    """Count one hit on `key`; 0 when within `limit` per `window` seconds, else seconds until it resets."""
    if limit <= 0:
        return 0
    now = time.monotonic()
    with _lock:
        if len(_windows) > MAX_KEYS_TRACKED:
            for k in [k for k, (start, _) in _windows.items() if now - start >= 3600]:
                _windows.pop(k, None)
        start, count = _windows.get(key, (now, 0))
        if now - start >= window:
            start, count = now, 0
        if count >= limit:
            _windows[key] = (start, count)
            return int(window - (now - start)) + 1
        _windows[key] = (start, count + 1)
        return 0


def check(key: str, limit: int, window: int, message: str) -> None:
    wait = hit(key, limit, window)
    if wait:
        raise Limited(message, wait)


def reset() -> None:
    with _lock:
        _windows.clear()


def api_call(user_id: int | None, ip: str, write: bool, count: bool = True) -> None:
    """Every /api/ request: per user when signed in, per address when not. With count=False only a
    change counts (the GitHub proxy's reads)."""
    op = current_app.config["OP"]
    if user_id is None:
        check(f"anon:{ip}", op.rate_anon, 60, "too many requests from this address; wait a minute")
        return
    if count:
        check(f"api:{user_id}", op.rate_api, 60, "too many requests; wait a minute")
    if write:
        check(f"write:{user_id}", op.rate_writes, 60, "too many changes in a minute; wait a little")


def new_account(ip: str) -> None:
    """Before an account is created (not for MASON_ALLOW_USERS). PermissionError says why not."""
    from mason.server.models import User, db
    op = current_app.config["OP"]
    if op.max_users and db.query(User).count() >= op.max_users:
        raise PermissionError("this Mason has as many accounts as it takes; ask whoever runs it")
    if hit(f"signup-ip:{ip}", op.signups_per_ip, 3600):
        raise PermissionError("too many new accounts from this address; try again in an hour")
    if hit("signup", op.signups_per_hour, 3600):
        raise PermissionError("too many new accounts right now; try again in an hour")


def pending(model) -> None:
    """Refuse to start another sign-in while PENDING of them wait (old ones are deleted as they expire)."""
    from mason.server.models import db
    if db.query(model).count() >= PENDING:
        raise Limited("too many sign-ins in progress; try again in a few minutes", 60)
