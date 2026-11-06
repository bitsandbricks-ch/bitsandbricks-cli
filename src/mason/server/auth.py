"""Who is calling: a browser session (cookie) or an API key (Authorization: Bearer mason_...).

- Session: after an OAuth login at GitHub or GitLab. The cookie is HttpOnly, Secure (__Host-),
  SameSite=Lax and holds a random value; the database keeps only its SHA-256. Sessions end after
  MASON_IDLE_HOURS without use and MASON_SESSION_DAYS at the latest.
- API key: for the CLI and TUI (`mason login`), read or write, optionally expiring, revocable.

State-changing requests with a cookie must carry X-CSRF-Token (the session's token, which only
same-origin script can read from /api/v1/me) and, when the browser sends one, a same-origin Origin.

The throttle is adapted from labdex (labdex/auth.py), relicensed into mason (formerly bitsandbricks)
under MIT by its author.
"""

from __future__ import annotations

import functools
import time
from collections import defaultdict
from datetime import timedelta

from flask import abort, current_app, g, jsonify, request

from mason.server import security
from mason.server.models import ApiKey, Audit, Session, User, db, utcnow

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SCOPES = {"read": 0, "write": 1}

# ── throttling (device codes, logins, bad keys) ─────────────────────────────
_failures: dict[str, list[float]] = defaultdict(list)
WINDOW, LIMIT = 300, 10


def throttled(key: str, limit: int = LIMIT) -> int:
    """Seconds to wait before another attempt (0 = allowed)."""
    now = time.monotonic()
    recent = [t for t in _failures.get(key, []) if now - t < WINDOW]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    if len(recent) < limit:
        return 0
    return int(WINDOW - (now - recent[-limit])) + 1


def record_failure(key: str) -> None:
    if len(_failures) > 10000:
        now = time.monotonic()
        for k in [k for k, v in _failures.items() if not v or now - v[-1] >= WINDOW]:
            _failures.pop(k, None)
    _failures[key].append(time.monotonic())


def client_ip() -> str:
    return request.remote_addr or ""


# ── cookies ──────────────────────────────────────────────────────────────────


def cookie_name() -> str:
    return "__Host-mason" if current_app.config["OP"].secure_cookies else "mason_session"


def set_session_cookie(resp, value: str) -> None:
    op = current_app.config["OP"]
    resp.set_cookie(cookie_name(), value, httponly=True, secure=op.secure_cookies, samesite="Lax", path="/",
                    max_age=op.session_days * 86400)


def clear_session_cookie(resp) -> None:
    resp.delete_cookie(cookie_name(), path="/", secure=current_app.config["OP"].secure_cookies, samesite="Lax")


def start_session(resp, user: User) -> Session:
    raw = security.random_token()
    s = Session(id=security.sha256(raw), user_id=user.id, csrf=security.random_token(24), ip=client_ip(),
                user_agent=(request.headers.get("User-Agent") or "")[:300])
    db.add(s)
    user.last_login = utcnow()
    db.flush()
    keep = current_app.config["OP"].max_sessions  # the oldest sessions end first
    for old in db.query(Session).filter_by(user_id=user.id).order_by(Session.seen_at.desc()).offset(keep):
        db.delete(old)
    db.commit()
    set_session_cookie(resp, raw)
    return s


def end_session() -> None:
    s = getattr(g, "session", None)
    if s is not None:
        db.delete(s)
        db.commit()


# ── resolving the caller ─────────────────────────────────────────────────────


def _from_session() -> bool:
    raw = request.cookies.get(cookie_name())
    if not raw:
        return False
    s = db.get(Session, security.sha256(raw))
    if s is None:
        return False
    op = current_app.config["OP"]
    now = utcnow()
    if now - s.seen_at > timedelta(hours=op.idle_hours) or now - s.created_at > timedelta(days=op.session_days):
        db.delete(s)
        db.commit()
        return False
    user = db.get(User, s.user_id)
    if user is None:
        return False
    if now - s.seen_at > timedelta(minutes=1):
        s.seen_at = now
        db.commit()
    g.user, g.session, g.via, g.scope = user, s, "web", "write"
    return True


def _from_key() -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    full = header[7:].strip()
    prefix = security.key_prefix(full)
    ip = client_ip()
    if throttled(f"key:{ip}"):
        abort(429, "too many bad API keys from this address; wait a few minutes")
    key = db.query(ApiKey).filter_by(prefix=prefix).one_or_none() if prefix else None
    now = utcnow()
    if (key is None or key.revoked_at is not None or not security.same(key.hash, security.sha256(full))
            or (key.expires_at is not None and key.expires_at < now)):
        record_failure(f"key:{ip}")
        abort(401, "this API key isn't valid (revoked, expired or unknown)")
    user = db.get(User, key.user_id)
    if user is None:
        abort(401, "this API key's user is gone")
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1) or key.last_ip != ip:
        key.last_used_at, key.last_ip = now, ip
        db.commit()
    g.user, g.session, g.via, g.key = user, None, f"key:{key.prefix}", key
    g.scope = key.scope
    return True


def resolve() -> None:
    g.user = g.session = g.key = None
    g.via, g.scope = "", ""
    if not _from_key():
        _from_session()


def check_csrf() -> None:
    """Cookie-authenticated writes need the session's CSRF token and a same-origin Origin."""
    if request.method in SAFE_METHODS or g.get("session") is None:
        return
    token = request.headers.get("X-CSRF-Token", "")
    if not token or not security.same(token, g.session.csrf):
        abort(403, "missing or wrong CSRF token")
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != current_app.config["OP"].url:
        abort(403, "cross-site request refused")


def need(scope: str = "read"):
    """The endpoint needs a signed-in user whose session or key has at least `scope`."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*a, **kw):
            if g.get("user") is None:
                abort(401, "sign in first")
            if SCOPES.get(g.scope, -1) < SCOPES[scope]:
                abort(403, f"this API key is {g.scope}-only; this needs a {scope} key")
            return fn(*a, **kw)
        return inner
    return wrap


def can_write() -> bool:
    return SCOPES.get(g.get("scope") or "", -1) >= SCOPES["write"]


def audit(action: str, target: str = "", dry_run: bool = False, ok: bool = True, detail: str = "") -> None:
    """One audit row, bounded as retention.py says: hashed address, output cut, oldest rows dropped."""
    from mason.server import retention
    user_id = g.user.id if g.get("user") else None
    db.add(Audit(user_id=user_id, via=g.get("via") or "", ip=retention.audit_ip(client_ip()),
                 action=action, target=target[:300], dry_run=dry_run, ok=ok, detail=retention.cut(detail)))
    db.commit()
    retention.trim(user_id)


def error_json(err):
    code = getattr(err, "code", 500) or 500
    return jsonify({"error": getattr(err, "description", str(err)) or str(err)}), code
