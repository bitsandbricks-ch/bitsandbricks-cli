"""Signing in and everything that belongs to the user: logins, sessions, API keys, the CLI's
device login, settings, secrets and the audit log."""

from __future__ import annotations

import json
from datetime import timedelta

from flask import Blueprint, abort, current_app, g, jsonify, make_response, redirect, request

from mason.server import auth, limits, oauth, retention, security
from mason.server.auth import audit, need
from mason.server.context import clean_settings, user_settings
from mason.server.models import (
    ApiKey,
    Audit,
    DeviceGrant,
    DeviceSignIn,
    Identity,
    OAuthState,
    Secret,
    Session,
    User,
    db,
    utcnow,
)

bp = Blueprint("account", __name__, url_prefix="/api/v1")
STATE_COOKIE = "mason_oauth"
DEVICE_COOKIE = "mason_device"
DEVICE_MINUTES = 10


def secret_names() -> dict[str, str]:
    """{name: label} of the secrets plugins let users keep here."""
    from mason import plugins
    out = {}
    for p in plugins.load():
        try:
            out.update({n: f"{label} ({p.name})" for n, label in (p.web_secrets() if hasattr(p, "web_secrets")
                                                                  else [])})
        except Exception:  # a broken plugin must not break the operator
            continue
    return out


def body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        abort(400, "send a JSON object")
    return data


def iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


# ── OAuth ────────────────────────────────────────────────────────────────────


def sign_in_methods() -> dict[str, str]:
    """{provider: redirect | device} of the sign-ins that are set up."""
    op = current_app.config["OP"]
    return {p.name: p.method for p in (op.github, op.gitlab) if p.enabled}


@bp.get("/auth/providers")
def providers():
    op = current_app.config["OP"]
    return jsonify([{"name": p.name, "web": p.web, "enabled": p.enabled, "method": p.method}
                    for p in (op.github, op.gitlab)])


def safe_next(value: str | None) -> str:
    v = value or "/"
    return v if v.startswith("/") and not v.startswith("//") and "\\" not in v and len(v) < 200 else "/"


@bp.get("/auth/<name>/login")
def login(name: str):
    if oauth.redirect_provider(name) is None:
        abort(404, f"{name} sign-in isn't set up on this operator")
    ip = auth.client_ip()
    if auth.throttled(f"login:{ip}", 30):
        abort(429, "too many sign-ins from this address; wait a few minutes")
    auth.record_failure(f"login:{ip}")  # counts attempts, not failures: 30 per 5 minutes
    limits.pending(OAuthState)
    vault = current_app.config["VAULT"]
    state, binding = security.random_token(), security.random_token()
    verifier, challenge = security.pkce_pair()
    link = g.user.id if request.args.get("link") == "1" and g.get("user") and g.get("session") else None
    db.query(OAuthState).filter(OAuthState.created_at < utcnow() - timedelta(minutes=oauth.STATE_MINUTES)).delete()
    db.add(OAuthState(state_hash=security.sha256(state), provider=name, verifier_enc=vault.encrypt(verifier),
                      link_user_id=link, binding=security.sha256(binding),
                      return_to=safe_next(request.args.get("next"))))
    db.commit()
    resp = make_response(redirect(oauth.authorize_url(name, state, challenge)))
    op = current_app.config["OP"]
    resp.set_cookie(STATE_COOKIE, binding, httponly=True, secure=op.secure_cookies, samesite="Lax",
                    max_age=oauth.STATE_MINUTES * 60, path="/api/v1/auth/")
    return resp


def _fail(message: str):
    resp = make_response(redirect(f"/login?error={_q(message)}"))
    resp.delete_cookie(STATE_COOKIE, path="/api/v1/auth/")
    return resp


def _q(text: str) -> str:
    from urllib.parse import quote
    return quote(text[:200])


@bp.get("/auth/<name>/callback")
def callback(name: str):
    if oauth.redirect_provider(name) is None:
        abort(404)
    if request.args.get("error"):
        return _fail(request.args.get("error_description") or request.args["error"])
    state, code = request.args.get("state", ""), request.args.get("code", "")
    row = db.get(OAuthState, security.sha256(state)) if state else None
    binding = request.cookies.get(STATE_COOKIE, "")
    if row is not None:
        db.delete(row)
        db.commit()
    if (row is None or row.provider != name or not binding or not security.same(row.binding, security.sha256(binding))
            or utcnow() - row.created_at > timedelta(minutes=oauth.STATE_MINUTES)):
        return _fail("the sign-in expired or was started in another browser; try again")
    verifier = current_app.config["VAULT"].decrypt(row.verifier_enc)
    try:
        got = oauth.exchange(name, code, verifier)
        link_user = db.get(User, row.link_user_id) if row.link_user_id else None
        user = oauth.sign_in(name, got, link_user)
    except PermissionError as err:
        audit_anon(f"login.{name}", str(err), ok=False)
        return _fail(str(err))
    except RuntimeError as err:
        return _fail(str(err))
    resp = make_response(redirect(row.return_to or "/"))
    resp.delete_cookie(STATE_COOKIE, path="/api/v1/auth/")
    _signed_in(resp, name, user, link_user)
    return resp


def _signed_in(resp, name: str, user: User, link_user: User | None) -> None:
    """A new session for a sign-in (the old one goes); nothing new when a login was linked."""
    if link_user is None:
        if g.get("session") is not None:  # signing in again: the old session goes
            db.delete(g.session)
            db.commit()
        auth.start_session(resp, user)
    g.user, g.via = user, "web"
    oauth.forget_transports(user.id)
    audit(f"{'link' if link_user else 'login'}.{name}", user.identity(name).login if user.identity(name) else "")


# ── GitHub's device flow: show the code, the user enters it at github.com/login/device ──


def _device_provider(name: str):
    p = oauth.provider(name)
    if p is None or not p.device:
        abort(404, f"{name} device sign-in isn't set up on this operator")
    origin = request.headers.get("Origin")  # signed-out callers skip the CSRF check: same origin only
    if origin and origin.rstrip("/") != current_app.config["OP"].url:
        abort(403, "cross-site request refused")
    return p


@bp.post("/auth/<name>/device")
def device_sign_in(name: str):
    _device_provider(name)
    ip = auth.client_ip()
    if auth.throttled(f"login:{ip}", 30):
        abort(429, "too many sign-ins from this address; wait a few minutes")
    auth.record_failure(f"login:{ip}")  # counts attempts, like the redirect sign-in
    limits.pending(DeviceSignIn)
    data = body()
    link = g.user.id if data.get("link") and g.get("user") and g.get("session") else None
    try:
        got = oauth.device_start(name)
    except RuntimeError as err:
        abort(502, str(err))
    db.query(DeviceSignIn).filter(DeviceSignIn.expires_at < utcnow()).delete()
    raw = security.random_token()
    minutes = max(1, min(int(got.get("expires_in") or 900) // 60, 30))
    row = DeviceSignIn(cookie_hash=security.sha256(raw), provider=name,
                       device_enc=current_app.config["VAULT"].encrypt(got["device_code"]),
                       user_code=str(got.get("user_code") or "")[:20],
                       verification_uri=str(got.get("verification_uri") or "")[:300], link_user_id=link,
                       return_to=safe_next(data.get("next")), interval=int(got.get("interval") or 5),
                       expires_at=utcnow() + timedelta(minutes=minutes))
    db.add(row)
    db.commit()
    resp = make_response(jsonify({"user_code": row.user_code, "verification_uri": row.verification_uri,
                                  "expires_in": minutes * 60, "interval": row.interval}))
    op = current_app.config["OP"]
    resp.set_cookie(DEVICE_COOKIE, raw, httponly=True, secure=op.secure_cookies, samesite="Strict",
                    max_age=minutes * 60, path="/api/v1/auth/")
    return resp


def _device_end(row: DeviceSignIn | None, payload: dict, status: int = 200):
    if row is not None:
        db.delete(row)
        db.commit()
    resp = make_response(jsonify(payload), status)
    resp.delete_cookie(DEVICE_COOKIE, path="/api/v1/auth/")
    return resp


@bp.post("/auth/<name>/device/poll")
def device_sign_in_poll(name: str):
    _device_provider(name)
    raw = request.cookies.get(DEVICE_COOKIE, "")
    row = db.get(DeviceSignIn, security.sha256(raw)) if raw else None
    now = utcnow()
    if row is None or row.provider != name or row.expires_at < now:
        return _device_end(row, {"status": "expired", "error": "the sign-in expired or was started in another "
                                 "browser; start again"}, 410)
    if row.last_poll is not None and (now - row.last_poll).total_seconds() < row.interval - 1:
        return jsonify({"status": "pending", "interval": row.interval})  # GitHub would say slow_down
    row.last_poll = now
    db.commit()
    try:
        got = oauth.device_poll(name, current_app.config["VAULT"].decrypt(row.device_enc))
    except oauth.DeviceFlowError as err:
        return _device_end(row, {"status": "failed", "error": str(err)}, 400)
    except RuntimeError as err:
        abort(502, str(err))
    if got is None or "access_token" not in got:
        if got:
            row.interval = max(row.interval, int(got["interval"]))
            db.commit()
        return jsonify({"status": "pending", "interval": row.interval})
    link_user = db.get(User, row.link_user_id) if row.link_user_id else None
    try:
        user = oauth.sign_in(name, got, link_user)
    except PermissionError as err:
        audit_anon(f"login.{name}", str(err), ok=False)
        return _device_end(row, {"status": "failed", "error": str(err)}, 403)
    except RuntimeError as err:
        return _device_end(row, {"status": "failed", "error": str(err)}, 502)
    resp = _device_end(row, {"status": "done", "next": row.return_to or "/"})
    _signed_in(resp, name, user, link_user)
    return resp


def audit_anon(action: str, detail: str, ok: bool) -> None:
    db.add(Audit(user_id=None, via="web", ip=retention.audit_ip(auth.client_ip()), action=action,
                 detail=detail[:500], ok=ok))
    db.commit()
    retention.trim(None)


@bp.post("/auth/logout")
@need("read")
def logout():
    if g.get("session") is None:
        abort(400, "API keys don't log out: revoke the key instead")
    audit("logout")
    auth.end_session()
    resp = make_response(jsonify({"ok": True}))
    auth.clear_session_cookie(resp)
    return resp


# ── me, logins, sessions ─────────────────────────────────────────────────────


@bp.get("/me")
def me():
    if g.get("user") is None:
        return jsonify({"user": None, "providers": list(sign_in_methods()), "sign_in": sign_in_methods()})
    u = g.user
    return jsonify({
        "user": {"id": u.id, "name": u.name, "avatar_url": u.avatar_url, "is_admin": u.is_admin},
        "identities": [{"id": i.id, "provider": i.provider, "host": i.host, "login": i.login, "scopes": i.scopes,
                        "expires_at": iso(i.expires_at)} for i in u.identities],
        "providers": list(sign_in_methods()), "sign_in": sign_in_methods(),
        "via": g.via, "scope": g.scope,
        "csrf": g.session.csrf if g.get("session") is not None else None,
        "settings": user_settings(u),
    })


@bp.delete("/me/identities/<int:ident_id>")
@need("write")
def unlink(ident_id: int):
    ident = db.get(Identity, ident_id)
    if ident is None or ident.user_id != g.user.id:
        abort(404)
    if len(g.user.identities) < 2:
        abort(400, "this is your only login; delete your account instead")
    got = oauth.revoke(ident)
    db.delete(ident)
    db.commit()
    oauth.forget_transports(g.user.id)
    audit("unlink", f"{ident.provider}:{ident.login}", ok=got["revoked"], detail=_revoke_note(got))
    return jsonify({"ok": True, "revoked": [got]})


def _revoke_note(got: dict) -> str:
    who = f"{got['provider']}:{got['login']}"
    if got["revoked"]:
        return f"{who}: token revoked"
    return f"{who}: token not revoked by Mason; revoke it at {got['manual']}"


@bp.get("/sessions")
@need("read")
def sessions():
    rows = db.query(Session).filter_by(user_id=g.user.id).order_by(Session.seen_at.desc()).all()
    mine = g.session.id if g.get("session") is not None else ""
    return jsonify([{"id": s.id[:12], "current": s.id == mine, "created_at": iso(s.created_at),
                     "seen_at": iso(s.seen_at), "ip": s.ip, "user_agent": s.user_agent} for s in rows])


@bp.delete("/sessions/<sid>")
@need("write")
def end_other_session(sid: str):
    rows = [s for s in db.query(Session).filter_by(user_id=g.user.id) if s.id.startswith(sid) and len(sid) >= 12]
    if len(rows) != 1:
        abort(404)
    db.delete(rows[0])
    db.commit()
    audit("session.end", sid)
    return jsonify({"ok": True})


@bp.delete("/me")
@need("write")
def delete_me():
    if g.get("session") is None:
        abort(403, "delete your account in the browser")
    revoked = [oauth.revoke(i) for i in g.user.identities]
    oauth.forget_transports(g.user.id)
    retention.forget_user(g.user.id)  # the account's audit rows go with it
    # one row without name, user or address says it happened, and whether Mason could revoke the tokens
    left = [r["provider"] for r in revoked if not r["revoked"]]
    db.add(Audit(user_id=None, via="web", action="account.delete", ok=not left,
                 detail=f"tokens not revoked by Mason: {', '.join(left)}" if left else "tokens revoked"))
    db.delete(g.user)
    db.commit()
    resp = make_response(jsonify({"ok": True, "revoked": revoked}))
    auth.clear_session_cookie(resp)
    return resp


# ── API keys ─────────────────────────────────────────────────────────────────


def key_json(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": f"{security.KEY_PREFIX}{k.prefix}", "scope": k.scope,
            "created_at": iso(k.created_at), "expires_at": iso(k.expires_at), "last_used_at": iso(k.last_used_at),
            "last_ip": k.last_ip, "revoked": k.revoked_at is not None}


def check_key_room(user: User) -> None:
    """At most MASON_MAX_KEYS working keys; revoked and expired ones beyond twice that are deleted."""
    cap = current_app.config["OP"].max_keys
    now = utcnow()
    rows = db.query(ApiKey).filter_by(user_id=user.id).order_by(ApiKey.created_at.desc()).all()
    live = [k for k in rows if k.revoked_at is None and (k.expires_at is None or k.expires_at > now)]
    if len(live) >= cap:
        abort(409, f"you have {len(live)} API keys, the most Mason keeps; revoke one first")
    dead = [k for k in rows if k not in live]
    for k in dead[max(0, 2 * cap - len(live) - 1):]:
        db.delete(k)
    db.commit()


def valid_days(days, default=None):
    if days is None:
        return default
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
        abort(400, "days: 1 to 3650, or leave it out for no expiry")
    return days


def issue_key(user: User, name: str, scope: str, days: int | None) -> tuple[str, ApiKey]:
    check_key_room(user)
    full, prefix, hashed = security.new_api_key()
    k = ApiKey(user_id=user.id, name=name[:100] or "key", prefix=prefix, hash=hashed, scope=scope,
               expires_at=utcnow() + timedelta(days=days) if days else None)
    db.add(k)
    db.commit()
    return full, k


@bp.get("/keys")
@need("read")
def keys():
    rows = db.query(ApiKey).filter_by(user_id=g.user.id).order_by(ApiKey.created_at.desc()).all()
    return jsonify([key_json(k) for k in rows])


@bp.post("/keys")
@need("write")
def new_key():
    if g.get("session") is None:
        abort(403, "create API keys in the browser")
    data = body()
    scope = data.get("scope", "read")
    if scope not in ("read", "write"):
        abort(400, "scope is read or write")
    days = valid_days(data.get("days"))
    full, k = issue_key(g.user, str(data.get("name") or "key"), scope, days)
    audit("key.create", f"{k.name} ({scope})")
    return jsonify({"key": full, **key_json(k)}), 201


@bp.delete("/keys/<int:key_id>")
@need("write")
def revoke_key(key_id: int):
    k = db.get(ApiKey, key_id)
    if k is None or k.user_id != g.user.id:
        abort(404)
    k.revoked_at = utcnow()
    db.commit()
    audit("key.revoke", k.name)
    return jsonify({"ok": True})


# ── device login: `mason login https://operator` ───────────────────────────────


@bp.post("/device/code")
def device_code():
    ip = auth.client_ip()
    if auth.throttled(f"device:{ip}", 20):
        abort(429, "too many device logins from this address; wait a few minutes")
    auth.record_failure(f"device:{ip}")
    limits.pending(DeviceGrant)
    data = body()
    scope = data.get("scope", "write")
    if scope not in ("read", "write"):
        abort(400, "scope is read or write")
    db.query(DeviceGrant).filter(DeviceGrant.expires_at < utcnow()).delete()
    device = security.random_token()
    code = security.user_code()
    while db.query(DeviceGrant).filter_by(user_code=code).first() is not None:
        code = security.user_code()
    db.add(DeviceGrant(device_hash=security.sha256(device), user_code=code, scope=scope,
                       client_name=str(data.get("client") or "mason")[:100],
                       expires_at=utcnow() + timedelta(minutes=DEVICE_MINUTES)))
    db.commit()
    url = current_app.config["OP"].url
    return jsonify({"device_code": device, "user_code": code, "verification_uri": f"{url}/device",
                    "verification_uri_complete": f"{url}/device?code={code}", "interval": 5,
                    "expires_in": DEVICE_MINUTES * 60})


@bp.post("/device/token")
def device_token():
    device = str(body().get("device_code") or "")
    row = db.query(DeviceGrant).filter_by(device_hash=security.sha256(device)).one_or_none() if device else None
    if row is None or row.expires_at < utcnow():
        return jsonify({"error": "expired_token"}), 400
    now = utcnow()
    if row.last_poll and now - row.last_poll < timedelta(seconds=4):
        return jsonify({"error": "slow_down"}), 400
    row.last_poll = now
    db.commit()
    if row.denied:
        db.delete(row)
        db.commit()
        return jsonify({"error": "access_denied"}), 400
    if not row.approved_by:
        return jsonify({"error": "authorization_pending"}), 400
    key = current_app.config["VAULT"].decrypt(row.key_enc)
    user = db.get(User, row.approved_by)
    db.delete(row)  # the key is handed out once
    db.commit()
    return jsonify({"key": key, "scope": row.scope, "user": user.name if user else ""})


@bp.get("/device/info")
@need("read")
def device_info():
    ip = auth.client_ip()
    if auth.throttled(f"devcode:{ip}"):
        abort(429, "too many wrong codes; wait a few minutes")
    row = db.query(DeviceGrant).filter_by(user_code=str(request.args.get("code", "")).upper()).one_or_none()
    if row is None or row.expires_at < utcnow() or row.approved_by or row.denied:
        auth.record_failure(f"devcode:{ip}")
        abort(404, "no such code, or it expired")
    return jsonify({"user_code": row.user_code, "client": row.client_name, "scope": row.scope,
                    "created_at": iso(row.created_at)})


@bp.post("/device/approve")
@need("write")
def device_approve():
    if g.get("session") is None:
        abort(403, "approve device logins in the browser")
    data = body()
    code = str(data.get("user_code") or "").strip().upper()
    ip = auth.client_ip()
    if auth.throttled(f"devcode:{ip}"):
        abort(429, "too many wrong codes; wait a few minutes")
    row = db.query(DeviceGrant).filter_by(user_code=code).one_or_none() if code else None
    if row is None or row.expires_at < utcnow() or row.approved_by or row.denied:
        auth.record_failure(f"devcode:{ip}")
        abort(404, "no such code, or it expired")
    if data.get("deny"):
        row.denied = True
        db.commit()
        audit("device.deny", row.client_name)
        return jsonify({"ok": True})
    scope = data.get("scope") or row.scope
    if scope not in ("read", "write"):
        abort(400, "scope is read or write")
    full, k = issue_key(g.user, f"{row.client_name} (device login)", scope, valid_days(data.get("days"), 365))
    row.approved_by, row.scope = g.user.id, scope
    row.key_enc = current_app.config["VAULT"].encrypt(full)
    db.commit()
    audit("device.approve", f"{row.client_name} ({scope})")
    return jsonify({"ok": True, "key": key_json(k)})


# ── settings and secrets ─────────────────────────────────────────────────────


@bp.get("/settings")
@need("read")
def get_settings():
    return jsonify(user_settings(g.user))


@bp.put("/settings")
@need("write")
def put_settings():
    new = {**g.user.settings, **clean_settings(body())}
    if len(json.dumps(new)) > limits.SETTINGS_BYTES:
        abort(413, f"settings are limited to {limits.SETTINGS_BYTES // 1024} KB")
    g.user.settings = new
    db.commit()
    audit("settings", ", ".join(sorted(new)))
    return jsonify(user_settings(g.user))


@bp.get("/secrets")
@need("read")
def secrets_list():
    rows = {s.name: s for s in db.query(Secret).filter_by(user_id=g.user.id).all()}
    return jsonify([{"name": n, "label": label, "set": n in rows,
                     "updated_at": iso(rows[n].updated_at) if n in rows else None}
                    for n, label in secret_names().items()])


@bp.put("/secrets/<name>")
@need("write")
def secret_put(name: str):
    if name not in secret_names():
        abort(404, f"unknown secret {name}")
    value = str(body().get("value") or "")
    if not value or len(value) > 4000:
        abort(400, "value: 1 to 4000 characters")
    row = db.query(Secret).filter_by(user_id=g.user.id, name=name).one_or_none()
    if row is None:
        row = Secret(user_id=g.user.id, name=name, value_enc="")
        db.add(row)
    row.value_enc = current_app.config["VAULT"].encrypt(value)
    db.commit()
    audit("secret.set", name)
    return jsonify({"ok": True})


@bp.delete("/secrets/<name>")
@need("write")
def secret_delete(name: str):
    db.query(Secret).filter_by(user_id=g.user.id, name=name).delete()
    db.commit()
    audit("secret.delete", name)
    return jsonify({"ok": True})


def secret_of(user: User, name: str) -> str:
    row = db.query(Secret).filter_by(user_id=user.id, name=name).one_or_none()
    return current_app.config["VAULT"].decrypt(row.value_enc) if row else ""


# ── audit ────────────────────────────────────────────────────────────────────


@bp.get("/audit")
@need("read")
def audit_log():
    q = db.query(Audit)
    if not (g.user.is_admin and request.args.get("all") == "1"):
        q = q.filter(Audit.user_id == g.user.id)
    rows = q.order_by(Audit.id.desc()).limit(min(int(request.args.get("limit", 200)), 1000)).all()
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_({r.user_id for r in rows if r.user_id}))}
    return jsonify([{"at": iso(r.at), "user": names.get(r.user_id, ""), "via": r.via, "ip": r.ip, "action": r.action,
                     "target": r.target, "dry_run": r.dry_run, "ok": r.ok, "detail": r.detail} for r in rows])


@bp.get("/admin/users")
@need("admin")
def admin_users():
    rows = db.query(User).order_by(User.id).all()
    return jsonify([{"id": u.id, "name": u.name, "is_admin": u.is_admin, "last_login": iso(u.last_login),
                     "logins": [f"{i.provider}:{i.login}" for i in u.identities],
                     "keys": db.query(ApiKey).filter_by(user_id=u.id, revoked_at=None).count()} for u in rows])


@bp.get("/admin/stats")
@need("admin")
def admin_stats():
    return jsonify({"transports": oauth.transport_stats(), "users": db.query(User).count(),
                    "sessions": db.query(Session).count(),
                    "keys": db.query(ApiKey).filter_by(revoked_at=None).count()})
