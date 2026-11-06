"""The operator (web app) against tests/fake_gh: sign-in state, CSRF, API keys, the device login,
every command endpoint, the console, the proxy's allowlist, the defaults editor and the marketplace."""

from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("flask")
pytest.importorskip("sqlalchemy")
if sys.version_info < (3, 10):  # pragma: no cover
    pytest.skip("the operator needs Python 3.10+", allow_module_level=True)


@pytest.fixture()
def op(env, monkeypatch, tmp_path):
    for key in ("PATH", "FAKE_GH_LOG", "XDG_CONFIG_HOME", "MTR_OPEN", "MTR_PLUGINS"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv("MTR_DEFAULTS", raising=False)
    from mortar import config, transport
    from mortar.server import oauth, security
    from mortar.server import settings as S
    from mortar.server.app import create_app
    from mortar.server.models import Identity, Session, User, db

    settings = S.Settings(
        url="http://localhost:8080", data=tmp_path, database=f"sqlite:///{tmp_path / 'op.db'}", secret_key="k" * 40,
        github=S.Provider("github", "id", "secret", "https://github.com", "https://api.github.com", "repo"),
        gitlab=S.Provider("gitlab", "", "", "https://gitlab.com", "https://gitlab.com/api/v4", "api"),
        allow_users={("github", "mike")}, admins={("github", "mike")}, plugin_index=str(tmp_path / "index.json"))
    app = create_app(settings)
    vault = app.config["VAULT"]
    # every GitHub call goes to the fake gh, with the user's token in GH_TOKEN
    monkeypatch.setattr(oauth, "github", lambda user: transport.GhTransport("ghtoken") if user.identity("github")
                        else None)
    with app.app_context():
        user = User(name="Mike", is_admin=True)
        db.add(user)
        db.flush()
        db.add(Identity(user_id=user.id, provider="github", host="github.com", uid="1", login="mike",
                        access_enc=vault.encrypt("ghtoken")))
        user.settings = {"defaults": "acme/defaults"}
        raw = "cookie-value"
        db.add(Session(id=security.sha256(raw), user_id=user.id, csrf="csrf-token"))
        db.commit()
    client = app.test_client()
    client.set_cookie("mtr_session", raw, domain="localhost")
    config._loaded.clear()
    yield app, client
    transport.set_default(None)
    db.remove()


H = {"X-CSRF-Token": "csrf-token"}


def test_signed_out_and_headers(op):
    app, _ = op
    anon = app.test_client()
    r = anon.get("/api/v1/me")
    assert r.json["user"] is None and r.json["providers"] == ["github"]
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Frame-Options"] == "DENY"
    assert anon.get("/api/v1/status").status_code == 401
    r = anon.get("/api/v1/auth/github/login")
    assert r.status_code == 302 and "code_challenge_method=S256" in r.headers["Location"]
    assert "state=" in r.headers["Location"] and "mtr_oauth=" in r.headers["Set-Cookie"]
    assert anon.get("/api/v1/auth/github/callback?state=nope&code=x").headers["Location"].startswith("/login?error=")
    assert anon.get("/api/v1/auth/gitlab/login").status_code == 404  # not set up


def test_me_csrf_settings(op):
    _, c = op
    me = c.get("/api/v1/me").json
    assert me["user"]["name"] == "Mike" and me["csrf"] == "csrf-token" and me["identities"][0]["login"] == "mike"
    assert c.put("/api/v1/settings", json={"orgs": ["acme"]}).status_code == 403  # no CSRF token
    r = c.put("/api/v1/settings", json={"orgs": ["acme"]}, headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.put("/api/v1/settings", json={"defaults": "/etc"}, headers=H).status_code == 400  # no server folders
    assert c.put("/api/v1/settings", json={"orgs": "acme, mike"}, headers=H).json["orgs"] == ["acme", "mike"]


def test_lists(op):
    _, c = op
    repos = c.get("/api/v1/repos").json
    assert "acme/plugin-a" in [r["repo"] for r in repos["repos"]] and repos["groups"] == ["plugins", "default"]
    st = c.get("/api/v1/status").json
    api = next(r for r in st["rows"] if r["repo"] == "acme/api")
    assert st["head"][:4] == ["group", "released", "PRs ready/draft", "CI"] and api["cells"][1] == "v1.10.0"
    prs = c.get("/api/v1/prs").json["prs"]
    assert {p["number"] for p in prs if p["repo"] == "acme/web"} == {7, 8, 9}
    assert next(p for p in prs if p["number"] == 9)["conflicts"]
    assert "blocked: required checks" in c.get("/api/v1/pr?repo=acme/web&number=7").json["text"]
    assert any(r["repo"] == "acme/web" for r in c.get("/api/v1/runs?repo=web").json["runs"])
    waits = c.get("/api/v1/approvals").json["approvals"]
    assert {(w["repo"], w["id"]) for w in waits} == {("acme/api", 5), ("acme/web", 6), ("acme/plugin-a", 12)}
    assert next(w for w in waits if w["id"] == 12)["blocked"] and not next(w for w in waits if w["id"] == 5)["blocked"]
    rel = c.get("/api/v1/releases?problems=1").json["releases"]
    assert any("missing *.zip" in " ".join(r["problems"]) for r in rel)
    assert c.get("/api/v1/packages").json["packages"]
    sec = {s["repo"]: s for s in c.get("/api/v1/security").json["security"]}
    assert sec["acme/api"]["alerts"][0]["severity"] == "high"
    groups = c.get("/api/v1/config").json["groups"]
    assert groups[0]["from"] == "acme/plugin-tpl"


def test_changes_are_dry_runs_and_audited(op):
    _, c = op
    r = c.post("/api/v1/merge", json={"targets": ["web#8", "web#9"], "dry_run": True}, headers=H)
    assert "draft, left open" in r.json["text"] and "has conflicts" in r.json["text"]
    text = c.post("/api/v1/doctor", json={"repos": ["web"]}, headers=H).json["text"]
    assert "delete_branch_on_merge is false, defaults has true" in text
    text = c.post("/api/v1/doctor", json={"repos": ["b"], "fix": True, "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] PATCH /repos/acme/plugin-b" in text
    text = c.post("/api/v1/sync", json={"repos": ["b"], "dry_run": True}, headers=H).json["text"]
    assert "update LICENSE" in text and "[dry-run] PR on mtr/sync-" in text
    text = c.post("/api/v1/new", json={"repo": "acme/plugin-c", "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] create private acme/plugin-c from the template acme/plugin-tpl" in text
    text = c.post("/api/v1/approve", json={"repo": "acme/api", "id": 5, "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] approve production for acme/api main" in text
    got = c.get("/api/v1/issues?mine=1").json
    assert [(i["repo"], i["number"]) for i in got["issues"]] == [("acme/web", 3)] and got["labels"] == ["bug"]
    assert "I can reproduce it." in c.get("/api/v1/issue?repo=acme/web&number=3").json["text"]
    text = c.post("/api/v1/issues", json={"repo": "acme/web", "title": "Crash", "labels": ["bug"], "dry_run": True},
                  headers=H).json["text"]
    assert "[dry-run] POST /repos/acme/web/issues" in text
    text = c.post("/api/v1/issue", json={"repo": "acme/web", "number": 3, "action": "close", "dry_run": True},
                  headers=H).json["text"]
    assert "[dry-run] PATCH /repos/acme/web/issues/3" in text
    assert c.post("/api/v1/issues", json={"repo": "acme/web", "title": " "}, headers=H).status_code == 400
    wf = {w["repo"]: w for w in c.get("/api/v1/workflows").json["workflows"]}
    assert wf["acme/api"] == {"repo": "acme/api", "workflows": 0, "findings": []}
    assert {f["kind"] for f in wf["acme/web"]["findings"]} == {"pinned", "permissions", "pull_request_target",
                                                              "injection"}
    text = c.post("/api/v1/workflows/pin", json={"repos": ["web"], "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] PUT /repos/acme/web/contents/.github/workflows/ci.yml" in text
    log = c.get("/api/v1/audit").json
    done = {a["action"] for a in log}
    assert {"merge", "doctor.fix", "sync", "new", "approve", "workflows.pin", "issue.create", "issue.close"} <= done
    assert all(a["dry_run"] for a in log if a["action"] in ("merge", "sync"))


def test_console(op):
    _, c = op
    out = c.post("/api/v1/console", json={"line": "mtr status"}, headers=H).json["text"]
    assert "acme/api" in out and "v1.10.0" in out
    assert "draft, left open" in c.post("/api/v1/console", json={"line": "mtr merge --dry-run web#8"},
                                        headers=H).json["text"]
    assert c.post("/api/v1/console", json={"line": "mtr init /etc"}, headers=H).status_code == 400
    assert c.post("/api/v1/console", json={"line": "rm -rf /"}, headers=H).status_code == 400
    assert "usage" in c.post("/api/v1/console", json={"line": "mtr status --nope"}, headers=H).json["text"]


def test_api_keys_and_device_login(op):
    app, c = op
    r = c.post("/api/v1/keys", json={"name": "laptop", "scope": "read"}, headers=H)
    key = r.json["key"]
    assert key.startswith("mtr_") and r.status_code == 201
    k = app.test_client()
    auth = {"Authorization": f"Bearer {key}"}
    assert k.get("/api/v1/me", headers=auth).json["scope"] == "read"
    assert k.post("/api/v1/merge", json={"targets": ["web#7"]}, headers=auth).status_code == 403  # read key
    assert k.post("/api/v1/merge", json={"targets": ["web#7"], "dry_run": True}, headers=auth).status_code == 200
    assert k.post("/api/v1/keys", json={"name": "x"}, headers=auth).status_code == 403  # keys come from the browser
    assert k.get("/api/v1/me", headers={"Authorization": "Bearer mtr_deadbeef_nope"}).status_code == 401
    c.delete(f"/api/v1/keys/{r.json['id']}", headers=H)
    assert k.get("/api/v1/status", headers=auth).status_code == 401  # revoked

    dev = app.test_client()
    code = dev.post("/api/v1/device/code", json={"scope": "write"}).json
    assert dev.post("/api/v1/device/token", json={"device_code": code["device_code"]}).json["error"] == \
        "authorization_pending"
    assert c.get(f"/api/v1/device/info?code={code['user_code']}").json["scope"] == "write"
    assert c.post("/api/v1/device/approve", json={"user_code": code["user_code"]}, headers=H).json["ok"]
    from mortar.server.models import DeviceGrant, db
    with app.app_context():
        db.query(DeviceGrant).update({"last_poll": None})
        db.commit()
    got = dev.post("/api/v1/device/token", json={"device_code": code["device_code"]}).json
    assert got["key"].startswith("mtr_") and got["scope"] == "write"
    assert dev.post("/api/v1/device/token", json={"device_code": code["device_code"]}).json["error"] == \
        "expired_token"  # handed out once


def test_proxy_allowlist():
    from mortar.server.api.proxy import allowed
    assert allowed("GET", "/repos/acme/web/pulls")
    assert allowed("PATCH", "/repos/acme/web")
    assert allowed("POST", "/repos/acme/web/actions/runs/5/pending_deployments")
    assert not allowed("DELETE", "/repos/acme/web")  # never deletes a repo
    assert not allowed("PUT", "/repos/acme/web/collaborators/eve")
    assert not allowed("GET", "/repos/acme/web/actions/secrets")
    assert not allowed("POST", "/user/keys")
    assert not allowed("GET", "/admin/users")


def test_defaults_editor(op):
    _, c = op
    d = c.get("/api/v1/defaults").json
    assert d["repo"] == "acme/defaults" and d["path"] == "mortar.toml" in d["files"]
    assert d["problems"] == []
    bad = c.post("/api/v1/defaults/validate", json={"path": "mortar.toml",
                                                    "text": '[[group]]\nname="a"\nmatch={nope=1}\n'}, headers=H).json
    assert "match doesn't know nope" in bad["problems"][0]
    bad = c.post("/api/v1/defaults/validate", headers=H, json={
        "path": "mortar.toml", "text": '[[group]]\nname="a"\nskip=["nope"]\nvisibility="x"\nactions={y=1}\n'}).json
    assert [p.split(": ")[1][:14] for p in bad["problems"]] == ["skip doesn't k", "visibility is ", "actions knows "]
    rs = c.post("/api/v1/defaults/validate", json={"path": "rulesets/default/x.json", "text": '{"target": "x"}'},
               headers=H).json
    assert "a ruleset needs a name" in rs["problems"]
    assert c.put("/api/v1/defaults/file", json={"path": "../etc/passwd", "text": "x"}, headers=H).status_code == 400
    assert any(t["kind"] == "ruleset" for t in c.get("/api/v1/defaults/templates").json)
    got = c.post("/api/v1/defaults/ruleset-from", json={"repo": "acme/api"}, headers=H).json
    assert got[0]["path"] == "rulesets/default/main.json" and json.loads(got[0]["text"])["name"] == "main"


def test_market(op, tmp_path):
    app, c = op
    from mortar import market
    pem, pub = market.keygen()
    doc = {"name": "test", "plugins": [{"name": "demo", "package": "demo-pkg", "version": "1.0",
                                        "source": "git+https://example.org/demo@v1"}]}
    (tmp_path / "index.json").write_text(json.dumps(doc))
    got = c.get("/api/v1/market").json
    assert got["status"] == "unverified" and got["plugins"][0]["name"] == "demo" and not got["can_install"]
    (tmp_path / "index.json").write_text(json.dumps(market.sign(doc, pem)))
    app.config["OP"].plugin_index_keys = [pub]
    assert c.get("/api/v1/market").json["status"] == "verified"
    tampered = market.sign(doc, pem)
    tampered["plugins"][0]["source"] = "git+https://evil.example/x"
    assert market.verify(tampered, market.trusted_keys([pub]))[0] == "unverified"
    assert c.post("/api/v1/market/demo/install", headers=H).status_code == 403  # installing from the web is off


def test_stats(op):
    _, c = op
    m = c.get("/api/v1/metrics?days=30").json
    assert m["days"] == 30 and "merged_by_week" in m["totals"] and m["repos"]


# ── sign-in: settings, open sign-in, GitHub's device flow, admins ────────────


def test_settings_defaults(monkeypatch, tmp_path):
    from mortar.server import settings as S
    for k in ("MTR_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_URL", "GITLAB_CLIENT_ID",
              "GITLAB_CLIENT_SECRET", "MTR_ALLOW_USERS", "MTR_ALLOW_ORGS", "MTR_ADMINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MTR_DATA", str(tmp_path))
    op = S.load()
    assert op.url == "http://localhost:8080" and op.problems() == []  # no MTR_DEV, no allow lists needed
    assert op.github.client_id == S.GITHUB_CLIENT_ID == "Ov23liFJgLtIjB3ZtuOy"
    assert op.github.method == "device" and not op.gitlab.enabled and op.open_sign_in
    monkeypatch.setenv("GITHUB_CLIENT_ID", "mine")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s")
    monkeypatch.setenv("GITLAB_CLIENT_ID", "gl")
    monkeypatch.setenv("GITLAB_CLIENT_SECRET", "gs")
    monkeypatch.setenv("MTR_ALLOW_ORGS", "acme")
    op = S.load()
    assert (op.github.client_id, op.github.method, op.gitlab.method) == ("mine", "redirect", "redirect")
    assert not op.open_sign_in
    monkeypatch.delenv("GITHUB_CLIENT_ID")
    monkeypatch.setenv("GITHUB_URL", "https://ghe.example.org")  # the public app means nothing there
    assert S.load().github.client_id == ""
    for url in ("http://localhost:8080", "http://127.0.0.1", "http://192.168.1.5:8080", "http://10.0.0.2",
                "http://[::1]:8080", "http://nas.local", "http://mortar.lan", "http://box.home.arpa",
                "https://mortar.example.org"):
        op.url = url
        assert op.problems() == [], url
    for url in ("http://mortar.example.org", "http://8.8.8.8", "http://local.example.org", "mortar.example.org"):
        op.url = url
        assert op.problems(), url


class FakeGitHub:
    """GitHub's device flow and /user, answering in order."""

    def __init__(self, login="ann", uid="77"):
        self.answers = []
        self.calls = []
        self.login, self.uid = login, uid

    def __call__(self, method, url, data=None, token=None, headers=None):
        self.calls.append((method, url, dict(data or {})))
        if url.endswith("/login/device/code"):
            return 200, {"device_code": "dev-123", "user_code": "ABCD-1234", "interval": 5, "expires_in": 900,
                         "verification_uri": "https://github.com/login/device"}
        if url.endswith("/login/oauth/access_token"):
            return self.answers.pop(0)
        if url.endswith("/user"):
            return 200, {"id": int(self.uid), "login": self.login, "name": self.login.title(), "avatar_url": ""}
        return 404, {}


def _poll(app, client):
    from mortar.server.models import DeviceSignIn, db
    with app.app_context():  # pretend the interval has passed
        db.query(DeviceSignIn).update({"last_poll": None})
        db.commit()
    return client.post("/api/v1/auth/github/device/poll", json={})


def test_device_sign_in_is_open_to_anyone(op, monkeypatch):
    app, mike = op
    from mortar.server import oauth
    app.config["OP"].allow_users = set()  # no allow lists: anyone may sign in
    fake = FakeGitHub()
    monkeypatch.setattr(oauth, "_http", fake)
    ann = app.test_client()
    assert ann.get("/api/v1/me").json["sign_in"] == {"github": "redirect"}
    assert ann.post("/api/v1/auth/github/device", json={}, headers={"Origin": "https://evil.example"}).status_code \
        == 403
    assert ann.post("/api/v1/auth/github/device/poll", json={}).json["status"] == "expired"  # nothing started
    r = ann.post("/api/v1/auth/github/device", json={"next": "/prs"})
    assert r.json["user_code"] == "ABCD-1234" and r.json["verification_uri"] == "https://github.com/login/device"
    assert "mtr_device=" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    assert fake.calls[0][2] == {"client_id": "id", "scope": "repo"}
    assert "dev-123" not in r.get_data(as_text=True)  # GitHub's device code stays on the server
    fake.answers = [(200, {"error": "authorization_pending"}), (200, {"error": "slow_down", "interval": 10}),
                    (200, {"access_token": "ann-token", "scope": "repo"})]
    assert _poll(app, ann).json == {"status": "pending", "interval": 5}
    assert ann.post("/api/v1/auth/github/device/poll", json={}).json["status"] == "pending"  # too soon: no call
    assert len([c for c in fake.calls if c[1].endswith("access_token")]) == 1
    assert _poll(app, ann).json == {"status": "pending", "interval": 10}
    got = _poll(app, ann)
    assert got.json == {"status": "done", "next": "/prs"}
    assert any("mtr_session=" in h for h in got.headers.getlist("Set-Cookie"))
    assert fake.calls[-2][2]["grant_type"] == oauth.DEVICE_GRANT and "client_secret" not in fake.calls[-2][2]
    me = ann.get("/api/v1/me").json
    assert me["user"]["name"] == "Ann" and not me["user"]["is_admin"] and me["identities"][0]["login"] == "ann"
    # each user sees only their own keys, sessions and audit log
    mike.post("/api/v1/keys", json={"name": "mike-key"}, headers=H)
    assert ann.get("/api/v1/keys").json == []
    assert len(ann.get("/api/v1/sessions").json) == 1
    assert {a["action"] for a in ann.get("/api/v1/audit").json} == {"login.github"}
    assert ann.get("/api/v1/admin/users").status_code == 403  # only admins
    assert _poll(app, ann).status_code == 403  # signed in now: writes need the CSRF token
    r = ann.post("/api/v1/auth/github/device/poll", json={}, headers={"X-CSRF-Token": me["csrf"]})
    assert r.json["status"] == "expired"  # the cookie was for one sign-in


def test_device_sign_in_respects_allow_lists_and_admins(op, monkeypatch):
    app, mike = op
    from mortar.server import oauth
    fake = FakeGitHub(login="eve", uid="66")
    monkeypatch.setattr(oauth, "_http", fake)
    eve = app.test_client()  # MTR_ALLOW_USERS=github:mike
    eve.post("/api/v1/auth/github/device", json={})
    fake.answers = [(200, {"access_token": "eve-token"})]
    r = _poll(app, eve)
    assert r.status_code == 403 and "may not use this operator" in r.json["error"]
    assert eve.get("/api/v1/me").json["user"] is None
    eve.post("/api/v1/auth/github/device", json={})
    fake.answers = [(200, {"error": "access_denied"})]
    assert _poll(app, eve).json == {"status": "failed", "error": "you cancelled the sign-in at GitHub"}
    # admins come only from MTR_ADMINS, also for an existing session
    assert mike.get("/api/v1/me").json["user"]["is_admin"]
    app.config["OP"].admins = set()
    assert not mike.get("/api/v1/me").json["user"]["is_admin"]
    assert mike.get("/api/v1/admin/users").status_code == 403


def test_device_only_github(op, monkeypatch):
    app, _ = op
    gh = app.config["OP"].github
    gh.client_secret = ""  # only the public client ID: device flow, no redirect sign-in
    anon = app.test_client()
    assert anon.get("/api/v1/me").json["sign_in"] == {"github": "device"}
    assert anon.get("/api/v1/auth/github/login").status_code == 404
    assert anon.get("/api/v1/auth/github/callback?state=x&code=y").status_code == 404
    from mortar.server import oauth
    monkeypatch.setattr(oauth, "_http", lambda *a, **k: (400, {"error": "device_flow_disabled"}))
    r = anon.post("/api/v1/auth/github/device", json={})
    assert r.status_code == 502 and "device flow switched on" in r.json["error"]
    assert anon.post("/api/v1/auth/gitlab/device", json={}).status_code == 404


def test_redirect_sign_in_github_and_gitlab(op, monkeypatch):
    app, _ = op
    from urllib.parse import parse_qs, urlsplit

    from mortar.server import oauth
    op_ = app.config["OP"]
    op_.gitlab.client_id, op_.gitlab.client_secret = "gl-id", "gl-secret"
    op_.allow_users = set()
    anon = app.test_client()
    assert anon.get("/api/v1/me").json["sign_in"] == {"github": "redirect", "gitlab": "redirect"}
    for name, host in (("github", "https://github.com/login/oauth/authorize"),
                       ("gitlab", "https://gitlab.com/oauth/authorize")):
        r = anon.get(f"/api/v1/auth/{name}/login?next=/runs")
        loc = r.headers["Location"]
        q = parse_qs(urlsplit(loc).query)
        assert loc.startswith(host) and q["redirect_uri"] == [f"http://localhost:8080/api/v1/auth/{name}/callback"]
    state = q["state"][0]

    def fake(method, url, data=None, token=None, headers=None):
        if url == "https://gitlab.com/oauth/token":
            assert data["client_secret"] == "gl-secret" and data["grant_type"] == "authorization_code"
            return 200, {"access_token": "gl-token", "refresh_token": "r", "expires_in": 7200}
        if url == "https://gitlab.com/api/v4/user":
            return 200, {"id": 9, "username": "gil", "name": "Gil"}
        return 404, {}
    monkeypatch.setattr(oauth, "_http", fake)
    r = anon.get(f"/api/v1/auth/gitlab/callback?state={state}&code=abc")
    assert r.status_code == 302 and r.headers["Location"] == "/runs"
    me = anon.get("/api/v1/me").json
    assert me["identities"][0]["provider"] == "gitlab" and me["identities"][0]["login"] == "gil"
    assert not me["user"]["is_admin"]
