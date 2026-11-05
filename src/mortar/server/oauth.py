"""Sign in with GitHub or GitLab (OAuth 2 authorization code with PKCE and state), and each user's
own transports to GitHub and GitLab built from the stored, encrypted tokens.

The operator never has a token of its own: everything it does at GitHub or GitLab it does with the
token of the user who asked, so nobody sees or changes more through the operator than they could
themselves.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from datetime import timedelta

from flask import current_app

from mortar.server import security
from mortar.server.models import Identity, User, db, utcnow
from mortar.transport import HttpTransport

STATE_MINUTES = 10


def _http(method: str, url: str, data: dict | None = None, token: str | None = None,
          headers: dict | None = None) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "mortar-server")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - configured https endpoints
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
    except urllib.error.URLError as err:
        raise RuntimeError(f"{url}: {err.reason}") from err


def provider(name: str):
    op = current_app.config["OP"]
    p = {"github": op.github, "gitlab": op.gitlab}.get(name)
    if p is None or not p.enabled:
        return None
    return p


def redirect_uri(name: str) -> str:
    return f"{current_app.config['OP'].url}/api/v1/auth/{name}/callback"


def authorize_url(name: str, state: str, challenge: str) -> str:
    p = provider(name)
    params = {"client_id": p.client_id, "redirect_uri": redirect_uri(name), "state": state, "scope": p.scopes,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    if name == "github":
        params["allow_signup"] = "false"
        return f"{p.web}/login/oauth/authorize?{urllib.parse.urlencode(params)}"
    params["response_type"] = "code"
    return f"{p.web}/oauth/authorize?{urllib.parse.urlencode(params)}"


def exchange(name: str, code: str, verifier: str) -> dict:
    """The token answer: access_token, and for GitLab (and expiring GitHub App tokens) refresh_token, expires_in."""
    p = provider(name)
    data = {"client_id": p.client_id, "client_secret": p.client_secret, "code": code,
            "redirect_uri": redirect_uri(name), "code_verifier": verifier}
    url = f"{p.web}/login/oauth/access_token" if name == "github" else f"{p.web}/oauth/token"
    if name == "gitlab":
        data["grant_type"] = "authorization_code"
    status, got = _http("POST", url, data)
    if status != 200 or not got.get("access_token"):
        raise RuntimeError(f"{name} refused the login: {got.get('error_description') or got.get('error') or status}")
    return got


def profile(name: str, token: str) -> dict:
    """{uid, login, name, avatar_url} of the token's user."""
    p = provider(name)
    status, me = _http("GET", f"{p.api}/user", token=token)
    if status != 200 or "id" not in me:
        raise RuntimeError(f"couldn't read your {name} profile ({status})")
    return {"uid": str(me["id"]), "login": me.get("login") or me.get("username") or "",
            "name": me.get("name") or "", "avatar_url": me.get("avatar_url") or ""}


def member_of(name: str, token: str, uid: str, org: str) -> bool:
    p = provider(name)
    if name == "github":
        status, got = _http("GET", f"{p.api}/user/memberships/orgs/{urllib.parse.quote(org)}", token=token)
        return status == 200 and got.get("state") == "active"
    status, _ = _http("GET", f"{p.api}/groups/{urllib.parse.quote(org, safe='')}/members/all/{uid}", token=token)
    return status == 200


def allowed(name: str, login: str, token: str, uid: str) -> bool:
    op = current_app.config["OP"]
    who = {(name, login.lower()), ("*", login.lower())}
    if op.allow_anyone or who & op.allow_users or who & op.admins:
        return True
    return any(prov in (name, "*") and member_of(name, token, uid, org) for prov, org in op.allow_orgs)


def is_admin(name: str, login: str) -> bool:
    return bool({(name, login.lower()), ("*", login.lower())} & current_app.config["OP"].admins)


def sign_in(name: str, got: dict, link_user: User | None) -> User:
    """Find or create the user of this login; store the (encrypted) tokens."""
    vault = current_app.config["VAULT"]
    p = provider(name)
    token = got["access_token"]
    me = profile(name, token)
    ident = db.query(Identity).filter_by(provider=name, host=p.host, uid=me["uid"]).one_or_none()
    if link_user is not None:
        if ident is not None and ident.user_id != link_user.id:
            raise PermissionError(f"this {name} login belongs to another operator user")
        user = link_user
    elif ident is not None:
        user = ident.user
    else:
        if not allowed(name, me["login"], token, me["uid"]):
            raise PermissionError(f"{name} user {me['login']} may not use this operator")
        user = User(name=me["name"] or me["login"], avatar_url=me["avatar_url"])
        db.add(user)
        db.flush()
    if ident is None:
        ident = Identity(user_id=user.id, provider=name, host=p.host, uid=me["uid"], login=me["login"])
        db.add(ident)
    elif link_user is None and not allowed(name, me["login"], token, me["uid"]):
        raise PermissionError(f"{name} user {me['login']} may no longer use this operator")
    ident.login = me["login"]
    ident.access_enc = vault.encrypt(token)
    if got.get("refresh_token"):
        ident.refresh_enc = vault.encrypt(got["refresh_token"])
    ident.expires_at = utcnow() + timedelta(seconds=int(got["expires_in"])) if got.get("expires_in") else None
    ident.scopes = str(got.get("scope") or p.scopes)[:500]
    if is_admin(name, me["login"]):
        user.is_admin = True
    if not user.avatar_url:
        user.avatar_url = me["avatar_url"]
    db.commit()
    return user


def access_token(ident: Identity) -> str:
    """The identity's token, refreshed first when it is about to expire."""
    vault = current_app.config["VAULT"]
    if ident.expires_at and ident.expires_at - utcnow() < timedelta(minutes=2) and ident.refresh_enc:
        p = provider(ident.provider)
        data = {"client_id": p.client_id, "client_secret": p.client_secret, "grant_type": "refresh_token",
                "refresh_token": vault.decrypt(ident.refresh_enc)}
        url = f"{p.web}/login/oauth/access_token" if ident.provider == "github" else f"{p.web}/oauth/token"
        if ident.provider == "gitlab":
            data["redirect_uri"] = redirect_uri("gitlab")
        status, got = _http("POST", url, data)
        if status == 200 and got.get("access_token"):
            ident.access_enc = vault.encrypt(got["access_token"])
            if got.get("refresh_token"):
                ident.refresh_enc = vault.encrypt(got["refresh_token"])
            ident.expires_at = utcnow() + timedelta(seconds=int(got.get("expires_in") or 7200))
            db.commit()
        else:
            raise RuntimeError(f"your {ident.provider} login expired: sign in with {ident.provider} again")
    return vault.decrypt(ident.access_enc)


def revoke(ident: Identity) -> None:
    """Best effort: tell GitHub/GitLab the operator no longer needs the token."""
    p = provider(ident.provider)
    token = current_app.config["VAULT"].decrypt(ident.access_enc)
    if not p or not token:
        return
    try:
        if ident.provider == "gitlab":
            _http("POST", f"{p.web}/oauth/revoke", {"client_id": p.client_id, "client_secret": p.client_secret,
                                                    "token": token})
        else:
            import base64
            basic = base64.b64encode(f"{p.client_id}:{p.client_secret}".encode()).decode()
            req_body = json.dumps({"access_token": token}).encode()
            req = urllib.request.Request(f"{p.api}/applications/{p.client_id}/token", data=req_body, method="DELETE")
            req.add_header("Authorization", f"Basic {basic}")
            req.add_header("Accept", "application/vnd.github+json")
            req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(req, timeout=10).close()  # noqa: S310
    except (RuntimeError, urllib.error.URLError, OSError):
        pass


# ── each user's transports, kept so caches outlive one request ───────────────

_transports: OrderedDict = OrderedDict()
_tlock = threading.Lock()
MAX_TRANSPORTS = 300


def _kept(key: tuple, make):
    with _tlock:
        t = _transports.get(key)
        if t is not None:
            _transports.move_to_end(key)
            return t
        t = make()
        _transports[key] = t
        while len(_transports) > MAX_TRANSPORTS:
            _transports.popitem(last=False)
        return t


def forget_transports(user_id: int) -> None:
    with _tlock:
        for k in [k for k in _transports if k[1] == user_id]:
            _transports.pop(k, None)


def github(user: User):
    ident = user.identity("github")
    if ident is None:
        return None
    token = access_token(ident)
    op = current_app.config["OP"]
    return _kept(("github", user.id, security.sha256(token)),
                 lambda: HttpTransport(token, api_url=op.github.api, web=op.github.web, cache=op.cache,
                                       user_agent="mortar-server"))


def gitlab(user: User):
    from mortar.server.gitlab import GitLab
    ident = user.identity("gitlab")
    if ident is None:
        return None
    token = access_token(ident)
    op = current_app.config["OP"]
    return _kept(("gitlab", user.id, security.sha256(token)),
                 lambda: GitLab(token, api_url=op.gitlab.api, web=op.gitlab.web, cache=op.cache))


def transport_stats() -> list[dict]:
    with _tlock:
        return [{"provider": k[0], "user_id": k[1], **getattr(t, "stats", {}), "rate": getattr(t, "last_rate", {})}
                for k, t in _transports.items()]
