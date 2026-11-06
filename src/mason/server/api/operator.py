"""The operator panel: what concerns the whole server, for the users `mason operator add` made operators.

Operators are added and removed only on the host (`mason operator add|remove`), never from the web.
The panel needs a browser session (not an API key) and every change is audited. Operators see who has an
account and the server's numbers, and switch plugins off for everyone; they can also sign a user out
everywhere. They never see or change another user's settings, repos, secrets or audit log.
"""

from __future__ import annotations

import json
import os

from flask import Blueprint, abort, current_app, jsonify

from mason import plugins
from mason.server import oauth
from mason.server.api.account import body, iso
from mason.server.auth import audit, need_operator
from mason.server.models import ApiKey, Operator, Session, Setting, User, db, utcnow

bp = Blueprint("operator", __name__, url_prefix="/api/v1/operator")


def off_for_all() -> set[str]:
    """Plugins operators switched off for every user (MASON_PLUGINS_OFF ones never load at all)."""
    row = db.get(Setting, "plugins_off")
    try:
        got = set(json.loads(row.value)) if row else set()
    except ValueError:
        got = set()
    return got


def _users() -> list[dict]:
    ops = {o.user_id for o in db.query(Operator)}
    return [{"id": u.id, "name": u.name, "operator": u.id in ops, "created_at": iso(u.created_at),
             "last_login": iso(u.last_login), "logins": [f"{i.provider}:{i.login}" for i in u.identities],
             "keys": db.query(ApiKey).filter_by(user_id=u.id, revoked_at=None).count(),
             "sessions": db.query(Session).filter_by(user_id=u.id).count()}
            for u in db.query(User).order_by(User.id)]


@bp.get("")
@need_operator
def panel():
    op = current_app.config["OP"]
    off = off_for_all()
    return jsonify({
        "users": _users(),
        "stats": {"users": db.query(User).count(), "sessions": db.query(Session).count(),
                  "keys": db.query(ApiKey).filter_by(revoked_at=None).count(), "transports": oauth.transport_stats()},
        "problems": current_app.config["PROBLEMS"],
        "plugins": [{"name": p.name, "description": getattr(p, "description", ""), "off_for_all": p.name in off}
                    for p in plugins.load()],
        "broken": plugins.errors,
        "server": {"url": op.url, "sign_in": "open" if op.open_sign_in else "allow lists",
                   "allow_users": sorted(f"{a}:{b}" for a, b in op.allow_users),
                   "allow_orgs": sorted(f"{a}:{b}" for a, b in op.allow_orgs),
                   "max_users": op.max_users, "plugin_index": op.plugin_index,
                   "plugins_never_loaded": os.environ.get("MASON_PLUGINS_OFF", "")},
    })


@bp.put("/plugins/<name>")
@need_operator
def plugin_for_all(name: str):
    if name not in {p.name for p in plugins.load()}:
        abort(404)
    on = bool(body().get("on"))
    row = db.get(Setting, "plugins_off") or Setting(key="plugins_off")
    try:
        off = set(json.loads(row.value or "[]"))
    except ValueError:
        off = set()
    off = off - {name} if on else off | {name}
    row.value = json.dumps(sorted(off))
    db.add(row)
    db.commit()
    audit("operator.plugin", f"{name} {'on' if on else 'off'} for everyone")
    return panel()


@bp.post("/users/<int:user_id>/sign-out")
@need_operator
def sign_out(user_id: int):
    """End all of a user's sessions and revoke their API keys (their account and settings stay)."""
    user = db.get(User, user_id)
    if user is None:
        abort(404)
    sessions = db.query(Session).filter_by(user_id=user.id).delete()
    keys = db.query(ApiKey).filter_by(user_id=user.id, revoked_at=None).update({"revoked_at": utcnow()})
    db.commit()
    oauth.forget_transports(user.id)
    audit("operator.sign-out", f"user {user.id} ({user.name})", detail=f"{sessions} sessions, {keys} keys")
    return jsonify({"sessions": sessions, "keys": keys})
