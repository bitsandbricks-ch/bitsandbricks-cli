"""What Mason keeps, and for how long: the audit log stays bounded, and expired rows go.

- Audit rows keep a keyed hash of the caller's address, not the address (MASON_AUDIT_IPS: hash, none or
  full). The hash is an HMAC with the secret key, so it tells "same address as before" without letting
  anyone who reads the database or the log look up an address.
- Command output in an audit row is cut to MASON_AUDIT_OUTPUT characters (1000).
- Each user keeps at most MASON_AUDIT_ROWS rows (1000; the oldest go first); rows without a user (failed
  sign-ins) share one such budget. Rows older than MASON_AUDIT_DAYS (90) go.
- Deleting an account deletes its audit rows; one row without name, user or address says it happened.
- Every few minutes, housekeeping() also deletes expired sessions, sign-in states, device codes and API
  keys that were revoked or expired more than KEY_DAYS ago.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from datetime import timedelta

from flask import current_app

from mason.server.models import (
    ApiKey,
    Audit,
    DeviceGrant,
    DeviceSignIn,
    OAuthState,
    Session,
    db,
    utcnow,
)

IP_MODES = ("hash", "none", "full")
KEY_DAYS = 30  # revoked or expired keys stay this long, so users still see them in Settings
EVERY = 600  # seconds between housekeeping runs

_lock = threading.Lock()
_last = 0.0


def audit_ip(ip: str) -> str:
    """What an audit row keeps of the caller's address."""
    mode = current_app.config["OP"].audit_ips
    if not ip or mode == "none":
        return ""
    if mode == "full":
        return ip[:64]
    key = current_app.config["OP"].secret_key.encode()
    return "ip:" + hmac.new(key, b"mason-audit-ip:" + ip.encode(), hashlib.sha256).hexdigest()[:16]


def cut(detail: str) -> str:
    """Command output as an audit row keeps it: at most MASON_AUDIT_OUTPUT characters."""
    limit = current_app.config["OP"].audit_output
    detail = detail or ""
    if len(detail) <= limit:
        return detail
    return detail[:limit] + f"\n… ({len(detail) - limit} more characters not kept)"


def trim(user_id: int | None) -> None:
    """Keep the newest MASON_AUDIT_ROWS rows of this user (or of the rows without a user)."""
    keep = current_app.config["OP"].audit_rows
    mine = Audit.user_id.is_(None) if user_id is None else Audit.user_id == user_id
    cutoff = (db.query(Audit.id).filter(mine).order_by(Audit.id.desc()).offset(keep).limit(1).scalar())
    if cutoff is not None:
        db.query(Audit).filter(mine, Audit.id <= cutoff).delete(synchronize_session=False)
        db.commit()


def forget_user(user_id: int) -> None:
    """An account is deleted: its audit rows go (the caller commits)."""
    db.query(Audit).filter(Audit.user_id == user_id).delete(synchronize_session=False)


def housekeeping(force: bool = False) -> bool:
    """Delete what has expired; runs at most every EVERY seconds unless forced. True when it ran."""
    global _last
    now_s = time.monotonic()
    with _lock:
        if not force and now_s - _last < EVERY:
            return False
        _last = now_s
    op = current_app.config["OP"]
    now = utcnow()
    db.query(Audit).filter(Audit.at < now - timedelta(days=op.audit_days)).delete(synchronize_session=False)
    db.query(Session).filter((Session.seen_at < now - timedelta(hours=op.idle_hours))
                             | (Session.created_at < now - timedelta(days=op.session_days))
                             ).delete(synchronize_session=False)
    from mason.server.oauth import STATE_MINUTES
    db.query(OAuthState).filter(OAuthState.created_at < now - timedelta(minutes=STATE_MINUTES)
                                ).delete(synchronize_session=False)
    db.query(DeviceGrant).filter(DeviceGrant.expires_at < now).delete(synchronize_session=False)
    db.query(DeviceSignIn).filter(DeviceSignIn.expires_at < now).delete(synchronize_session=False)
    old = now - timedelta(days=KEY_DAYS)
    db.query(ApiKey).filter((ApiKey.revoked_at < old) | (ApiKey.expires_at < old)).delete(synchronize_session=False)
    db.commit()
    return True
