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
    for key in ("PATH", "FAKE_GH_LOG", "XDG_CONFIG_HOME", "MASON_OPEN", "MASON_PLUGINS"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv("MASON_DEFAULTS", raising=False)
    from mason import config, transport
    from mason.server import oauth, security
    from mason.server import settings as S
    from mason.server.app import create_app
    from mason.server.models import Identity, Session, User, db

    settings = S.Settings(
        url="http://localhost:8080", data=tmp_path, database=f"sqlite:///{tmp_path / 'op.db'}", secret_key="k" * 40,
        github=S.Provider("github", "id", "secret", "https://github.com", "https://api.github.com", "repo"),
        gitlab=S.Provider("gitlab", "", "", "https://gitlab.com", "https://gitlab.com/api/v4", "api"),
        allow_users={("github", "mike")}, plugin_index=str(tmp_path / "index.json"))
    from mason.server import limits
    limits.reset()
    app = create_app(settings)
    vault = app.config["VAULT"]
    # every GitHub call goes to the fake gh, with the user's token in GH_TOKEN
    monkeypatch.setattr(oauth, "github", lambda user: transport.GhTransport("ghtoken") if user.identity("github")
                        else None)
    with app.app_context():
        user = User(name="Mike")
        db.add(user)
        db.flush()
        db.add(Identity(user_id=user.id, provider="github", host="github.com", uid="1", login="mike",
                        access_enc=vault.encrypt("ghtoken")))
        user.settings = {"defaults": "acme/defaults"}
        raw = "cookie-value"
        db.add(Session(id=security.sha256(raw), user_id=user.id, csrf="csrf-token"))
        db.commit()
    client = app.test_client()
    client.set_cookie("mason_session", raw, domain="localhost")
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
    assert "state=" in r.headers["Location"] and "mason_oauth=" in r.headers["Set-Cookie"]
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
    assert "update LICENSE" in text and "[dry-run] PR on mason/sync-" in text
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
    out = c.post("/api/v1/console", json={"line": "mason status"}, headers=H).json["text"]
    assert "acme/api" in out and "v1.10.0" in out
    assert "draft, left open" in c.post("/api/v1/console", json={"line": "mason merge --dry-run web#8"},
                                        headers=H).json["text"]
    assert c.post("/api/v1/console", json={"line": "mason init /etc"}, headers=H).status_code == 400
    assert c.post("/api/v1/console", json={"line": "rm -rf /"}, headers=H).status_code == 400
    assert "usage" in c.post("/api/v1/console", json={"line": "mason status --nope"}, headers=H).json["text"]


def test_api_keys_and_device_login(op):
    app, c = op
    r = c.post("/api/v1/keys", json={"name": "laptop", "scope": "read"}, headers=H)
    key = r.json["key"]
    assert key.startswith("mason_") and r.status_code == 201
    k = app.test_client()
    auth = {"Authorization": f"Bearer {key}"}
    assert k.get("/api/v1/me", headers=auth).json["scope"] == "read"
    assert k.post("/api/v1/merge", json={"targets": ["web#7"]}, headers=auth).status_code == 403  # read key
    assert k.post("/api/v1/merge", json={"targets": ["web#7"], "dry_run": True}, headers=auth).status_code == 200
    assert k.post("/api/v1/keys", json={"name": "x"}, headers=auth).status_code == 403  # keys come from the browser
    assert k.get("/api/v1/me", headers={"Authorization": "Bearer mason_deadbeef_nope"}).status_code == 401
    c.delete(f"/api/v1/keys/{r.json['id']}", headers=H)
    assert k.get("/api/v1/status", headers=auth).status_code == 401  # revoked

    dev = app.test_client()
    code = dev.post("/api/v1/device/code", json={"scope": "write"}).json
    assert dev.post("/api/v1/device/token", json={"device_code": code["device_code"]}).json["error"] == \
        "authorization_pending"
    assert c.get(f"/api/v1/device/info?code={code['user_code']}").json["scope"] == "write"
    assert c.post("/api/v1/device/approve", json={"user_code": code["user_code"]}, headers=H).json["ok"]
    from mason.server.models import DeviceGrant, db
    with app.app_context():
        db.query(DeviceGrant).update({"last_poll": None})
        db.commit()
    got = dev.post("/api/v1/device/token", json={"device_code": code["device_code"]}).json
    assert got["key"].startswith("mason_") and got["scope"] == "write"
    assert dev.post("/api/v1/device/token", json={"device_code": code["device_code"]}).json["error"] == \
        "expired_token"  # handed out once


def test_proxy_allowlist():
    from mason.server.api.proxy import allowed
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
    assert d["repo"] == "acme/defaults" and d["path"] == "mason.toml" in d["files"]
    assert d["problems"] == []
    bad = c.post("/api/v1/defaults/validate", json={"path": "mason.toml",
                                                    "text": '[[group]]\nname="a"\nmatch={nope=1}\n'}, headers=H).json
    assert "match doesn't know nope" in bad["problems"][0]
    bad = c.post("/api/v1/defaults/validate", headers=H, json={
        "path": "mason.toml", "text": '[[group]]\nname="a"\nskip=["nope"]\nvisibility="x"\nactions={y=1}\n'}).json
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
    from mason import market
    pem, pub = market.keygen()
    doc = {"name": "test", "plugins": [{"name": "demo", "package": "demo-pkg", "version": "1.0",
                                        "source": "git+https://example.org/demo@v1"}]}
    (tmp_path / "index.json").write_text(json.dumps(doc))
    got = c.get("/api/v1/market").json
    assert got["status"] == "unverified" and got["plugins"][0]["name"] == "demo" and "can_install" not in got
    (tmp_path / "index.json").write_text(json.dumps(market.sign(doc, pem)))
    app.config["OP"].plugin_index_keys = [pub]
    assert c.get("/api/v1/market").json["status"] == "verified"
    tampered = market.sign(doc, pem)
    tampered["plugins"][0]["source"] = "git+https://evil.example/x"
    assert market.verify(tampered, market.trusted_keys([pub]))[0] == "unverified"
    assert c.post("/api/v1/market/demo/install", headers=H).status_code in (404, 405)  # never from the web


def test_stats(op):
    _, c = op
    m = c.get("/api/v1/metrics?days=30").json
    assert m["days"] == 30 and "merged_by_week" in m["totals"] and m["repos"]


# ── sign-in: settings, open sign-in, GitHub's device flow ───────────────────


def test_settings_defaults(monkeypatch, tmp_path):
    from mason.server import settings as S
    for k in ("MASON_URL", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "GITHUB_URL", "GITLAB_CLIENT_ID",
              "GITLAB_CLIENT_SECRET", "MASON_ALLOW_USERS", "MASON_ALLOW_ORGS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MASON_DATA", str(tmp_path))
    op = S.load()
    assert op.url == "http://localhost:8080" and op.problems() == []  # no MASON_DEV, no allow lists needed
    assert op.github.client_id == S.GITHUB_CLIENT_ID == "Ov23liFJgLtIjB3ZtuOy"
    assert op.github.method == "device" and not op.gitlab.enabled and op.open_sign_in
    monkeypatch.setenv("GITHUB_CLIENT_ID", "mine")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "s")
    monkeypatch.setenv("GITLAB_CLIENT_ID", "gl")
    monkeypatch.setenv("GITLAB_CLIENT_SECRET", "gs")
    monkeypatch.setenv("MASON_ALLOW_ORGS", "acme")
    op = S.load()
    assert (op.github.client_id, op.github.method, op.gitlab.method) == ("mine", "redirect", "redirect")
    assert not op.open_sign_in
    monkeypatch.delenv("GITHUB_CLIENT_ID")
    monkeypatch.setenv("GITHUB_URL", "https://ghe.example.org")  # the public app means nothing there
    assert S.load().github.client_id == ""
    for url in ("http://localhost:8080", "http://127.0.0.1", "http://192.168.1.5:8080", "http://10.0.0.2",
                "http://[::1]:8080", "http://nas.local", "http://mason.lan", "http://box.home.arpa",
                "https://mason.example.org"):
        op.url = url
        assert op.problems() == [], url
    for url in ("http://mason.example.org", "http://8.8.8.8", "http://local.example.org", "mason.example.org"):
        op.url = url
        assert op.problems(), url


def test_serve_port_sets_default_url(monkeypatch, tmp_path, capsys):
    from mason.server import app as A
    from mason.server import settings as S
    monkeypatch.delenv("MASON_URL", raising=False)
    monkeypatch.setenv("MASON_DATA", str(tmp_path))
    assert S.load().url == "http://localhost:8080"
    assert S.load(9000).url == "http://localhost:9000"
    monkeypatch.setenv("MASON_URL", "https://mason.example.org/")  # an explicit MASON_URL wins
    assert S.load(9000).url == "https://mason.example.org"
    monkeypatch.delenv("MASON_URL")
    ran = {}

    class FakeApp:
        def run(self, **kw):
            ran.update(kw)

    monkeypatch.setattr(A, "create_app", lambda op: ran.update(url=op.url) or FakeApp())
    from mason import cli
    cli.main(["serve", "--port", "9000"])
    assert (ran["url"], ran["port"], ran["host"]) == ("http://localhost:9000", 9000, "127.0.0.1")
    with pytest.raises(SystemExit):
        cli.main(["serve", "--check", "--port", "9001"])
    assert "url          http://localhost:9001" in capsys.readouterr().out


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
    from mason.server.models import DeviceSignIn, db
    with app.app_context():  # pretend the interval has passed
        db.query(DeviceSignIn).update({"last_poll": None})
        db.commit()
    return client.post("/api/v1/auth/github/device/poll", json={})


def test_device_sign_in_is_open_to_anyone(op, monkeypatch):
    app, mike = op
    from mason.server import oauth
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
    assert "mason_device=" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
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
    assert any("mason_session=" in h for h in got.headers.getlist("Set-Cookie"))
    assert fake.calls[-2][2]["grant_type"] == oauth.DEVICE_GRANT and "client_secret" not in fake.calls[-2][2]
    me = ann.get("/api/v1/me").json
    assert me["user"]["name"] == "Ann" and "is_admin" not in me["user"] and me["identities"][0]["login"] == "ann"
    # each user sees only their own keys, sessions and audit log
    mike.post("/api/v1/keys", json={"name": "mike-key"}, headers=H)
    assert ann.get("/api/v1/keys").json == []
    assert len(ann.get("/api/v1/sessions").json) == 1
    assert {a["action"] for a in ann.get("/api/v1/audit").json} == {"login.github"}
    assert {a["action"] for a in ann.get("/api/v1/audit?all=1").json} == {"login.github"}  # no "everyone"
    assert mike.get("/api/v1/admin/users").status_code == 404  # no admins, so no list of users
    assert mike.get("/api/v1/me").json["scope"] == "write"
    # plugins are switched per user: Ann's choice never reaches Mike ("everyone" is for operators, elsewhere)
    from mason import plugins

    class Demo:
        name = "demo"
        def tui_tabs(self): return []  # noqa: E301,E704
        def tui_checks(self): return []  # noqa: E301,E704
        def status_columns(self): return []  # noqa: E301,E704
    monkeypatch.setattr(plugins, "load", lambda: [Demo()])
    ah = {"X-CSRF-Token": me["csrf"]}
    assert ann.put("/api/v1/plugins/demo", json={"on": False, "everyone": False}, headers=ah).status_code == 200
    assert not next(p for p in ann.get("/api/v1/plugins").json["plugins"] if p["name"] == "demo")["on"]
    mine = next(p for p in mike.get("/api/v1/plugins").json["plugins"] if p["name"] == "demo")
    assert mine["on"] and not mine["off_for_all"]
    assert _poll(app, ann).status_code == 403  # signed in now: writes need the CSRF token
    r = ann.post("/api/v1/auth/github/device/poll", json={}, headers={"X-CSRF-Token": me["csrf"]})
    assert r.json["status"] == "expired"  # the cookie was for one sign-in


def test_device_sign_in_respects_allow_lists(op, monkeypatch):
    app, mike = op
    from mason.server import oauth
    fake = FakeGitHub(login="eve", uid="66")
    monkeypatch.setattr(oauth, "_http", fake)
    eve = app.test_client()  # MASON_ALLOW_USERS=github:mike
    eve.post("/api/v1/auth/github/device", json={})
    fake.answers = [(200, {"access_token": "eve-token"})]
    r = _poll(app, eve)
    assert r.status_code == 403 and "may not use this operator" in r.json["error"]
    assert eve.get("/api/v1/me").json["user"] is None
    eve.post("/api/v1/auth/github/device", json={})
    fake.answers = [(200, {"error": "access_denied"})]
    assert _poll(app, eve).json == {"status": "failed", "error": "you cancelled the sign-in at GitHub"}
    assert mike.get("/api/v1/me").json["user"]["name"] == "Mike"  # listed users still get in


def test_device_only_github(op, monkeypatch):
    app, _ = op
    gh = app.config["OP"].github
    gh.client_secret = ""  # only the public client ID: device flow, no redirect sign-in
    anon = app.test_client()
    assert anon.get("/api/v1/me").json["sign_in"] == {"github": "device"}
    assert anon.get("/api/v1/auth/github/login").status_code == 404
    assert anon.get("/api/v1/auth/github/callback?state=x&code=y").status_code == 404
    from mason.server import oauth
    monkeypatch.setattr(oauth, "_http", lambda *a, **k: (400, {"error": "device_flow_disabled"}))
    r = anon.post("/api/v1/auth/github/device", json={})
    assert r.status_code == 502 and "device flow switched on" in r.json["error"]
    assert anon.post("/api/v1/auth/gitlab/device", json={}).status_code == 404


def test_redirect_sign_in_github_and_gitlab(op, monkeypatch):
    app, _ = op
    from urllib.parse import parse_qs, urlsplit

    from mason.server import oauth
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
    assert "is_admin" not in me["user"]


def test_release_page_and_create(op):
    _, c = op
    page = c.get("/api/v1/release?repo=acme/api").json
    assert [r["tag"] for r in page["releases"]] == ["v1.10.0", "v1.2.0"] and page["selected"] == "v1.10.0"
    assert "- Faster startup" in page["text"] and page["suggest"] == "v1.10.1"
    page = c.get("/api/v1/release?repo=acme/api&tag=v1.2.0").json
    assert page["selected"] == "v1.2.0" and "prerelease" in page["text"]
    assert c.get("/api/v1/release?repo=acme/api&tag=v9.9.9").status_code == 404
    body = {"repo": "acme/api", "tag": "v1.11.0", "title": "Search", "notes": "- New", "draft": True, "dry_run": True}
    got = c.post("/api/v1/releases", json=body, headers=H).json
    assert "[dry-run] POST /repos/acme/api/releases" in got["text"] and "draft release v1.11.0" in got["text"]
    got = c.post("/api/v1/releases", json={**body, "dry_run": False}, headers=H).json
    assert got["ok"] and "created the draft release v1.11.0 of acme/api" in got["text"]
    got = c.post("/api/v1/releases", json={**body, "tag": "v1.10.0"}, headers=H).json
    assert not got["ok"] and "has a release v1.10.0 already" in got["text"]
    assert c.post("/api/v1/releases", json={**body, "tag": "no spaces"}, headers=H).status_code == 400
    assert any(a["action"] == "release.create" for a in c.get("/api/v1/audit").json)


def test_proxy_reads_through_the_users_cache_and_writes_make_it_stale(op, monkeypatch):
    from test_cache import Fake

    from mason.server import oauth
    from mason.transport import HttpTransport
    app, client = op
    api = Fake()
    try:
        t = HttpTransport("ghtoken", api_url=api.url, graphql_url=api.url + "/graphql", cache="60s")
        monkeypatch.setattr(oauth, "github", lambda user: t)
        r = client.get("/api/v1/github/rest/repos/acme/web/labels")
        assert r.status_code == 200 and r.headers.get("ETag")
        etag = r.headers["ETag"]
        r = client.get("/api/v1/github/rest/repos/acme/web/labels", headers={"If-None-Match": etag})
        assert r.status_code == 304
        assert len(api.hits("/repos/acme/web/labels")) == 1  # the second came from the cache
        r = client.post("/api/v1/github/rest/repos/acme/web/labels", json={"name": "bug"}, headers=H)
        assert r.status_code == 200
        client.get("/api/v1/github/rest/repos/acme/web/labels")
        got = api.hits("/repos/acme/web/labels")
        assert len(got) == 3 and got[-1][0] == "GET" and got[-1][2].get("If-None-Match") == etag
    finally:
        api.server.shutdown()
# ── revoking tokens when a login is disconnected or the account deleted ──────


def _add_gitlab(app, monkeypatch):
    from mason.server.models import Identity, User, db
    op = app.config["OP"]
    monkeypatch.setattr(op.gitlab, "client_id", "glid")
    monkeypatch.setattr(op.gitlab, "client_secret", "glsecret")
    with app.app_context():
        user = db.query(User).one()
        db.add(Identity(user_id=user.id, provider="gitlab", host="gitlab.com", uid="9", login="mike-gl",
                        access_enc=app.config["VAULT"].encrypt("gltoken")))
        db.commit()
        return [i.id for i in db.query(Identity).order_by(Identity.id)]


def test_unlink_revokes_with_a_client_secret(op, monkeypatch):
    app, c = op
    from mason.server import oauth
    calls = []
    monkeypatch.setattr(oauth, "_http", lambda method, url, data=None, **kw: calls.append((method, url, data, kw))
                        or (204 if method == "DELETE" else 200, {}))
    gh_id, gl_id = _add_gitlab(app, monkeypatch)
    r = c.delete(f"/api/v1/me/identities/{gl_id}", headers=H)
    assert r.json["revoked"] == [{"provider": "gitlab", "login": "mike-gl", "revoked": True, "manual": ""}]
    assert calls[-1][:2] == ("POST", "https://gitlab.com/oauth/revoke") and calls[-1][2]["token"] == "gltoken"
    # the last login goes with the account: GitHub's revoke API with the app's ID and secret
    r = c.delete("/api/v1/me", headers=H)
    assert r.json["revoked"][0]["revoked"] is True
    method, url, _, kw = calls[-1]
    assert (method, url) == ("DELETE", "https://api.github.com/applications/id/token")
    assert kw["json_body"] == {"access_token": "ghtoken"} and kw["headers"]["Authorization"].startswith("Basic ")


def test_device_flow_tokens_cant_be_revoked_and_say_so(op, monkeypatch):
    app, c = op
    from mason.server import oauth
    calls = []
    monkeypatch.setattr(oauth, "_http", lambda *a, **kw: calls.append(a) or (200, {}))
    monkeypatch.setattr(app.config["OP"].github, "client_secret", "")  # the built-in sign-in: no secret
    r = c.delete("/api/v1/me", headers=H)
    assert r.status_code == 200 and not calls
    assert r.json["revoked"] == [{"provider": "github", "login": "mike", "revoked": False,
                                  "manual": "https://github.com/settings/connections/applications/id"}]
    with app.app_context():
        from mason.server.models import Audit, Identity, db
        assert db.query(Identity).count() == 0  # Mason's copy is gone either way
        row = db.query(Audit).filter_by(action="account.delete").one()
        assert not row.ok and row.detail == "tokens not revoked by Mason: github" and row.user_id is None


def test_failed_revoke_gives_the_link(op, monkeypatch):
    app, c = op
    from mason.server import oauth
    monkeypatch.setattr(oauth, "_http", lambda *a, **kw: (500, {}))
    _, gl_id = _add_gitlab(app, monkeypatch)
    got = c.delete(f"/api/v1/me/identities/{gl_id}", headers=H).json["revoked"][0]
    assert got["revoked"] is False and got["manual"] == "https://gitlab.com/-/user_settings/applications"


# ── the audit log stays bounded ──────────────────────────────────────────────


def test_audit_hashes_ips_and_cuts_output(op, monkeypatch):
    app, c = op
    from mason.server.models import Audit, db
    op_settings = app.config["OP"]
    monkeypatch.setattr(op_settings, "audit_output", 50)
    c.post("/api/v1/doctor", json={"repos": ["b"], "fix": True, "dry_run": True}, headers=H,
           environ_base={"REMOTE_ADDR": "203.0.113.7"})
    with app.app_context():
        row = db.query(Audit).order_by(Audit.id.desc()).first()
        assert row.ip.startswith("ip:") and "203.0.113" not in row.ip and len(row.ip) == 19
        assert len(row.detail) < 120 and row.detail.endswith("more characters not kept)")
    # the same address hashes the same way, so users still see "same place as before"
    c.put("/api/v1/settings", json={"orgs": ["acme"]}, headers=H, environ_base={"REMOTE_ADDR": "203.0.113.7"})
    with app.app_context():
        a, b = db.query(Audit).order_by(Audit.id.desc()).limit(2)
        assert a.ip == b.ip
    monkeypatch.setattr(op_settings, "audit_ips", "none")
    c.put("/api/v1/settings", json={"orgs": ["acme"]}, headers=H)
    with app.app_context():
        assert db.query(Audit).order_by(Audit.id.desc()).first().ip == ""


def test_audit_keeps_the_newest_rows_and_purges_old_ones(op, monkeypatch):
    app, c = op
    from datetime import timedelta

    from mason.server import retention
    from mason.server.models import ApiKey, Audit, Session, User, db, utcnow
    monkeypatch.setattr(app.config["OP"], "audit_rows", 3)
    for n in range(5):
        c.put("/api/v1/settings", json={"orgs": [f"org{n}"]}, headers=H)
    with app.app_context():
        rows = db.query(Audit).filter(Audit.user_id.isnot(None)).order_by(Audit.id).all()
        assert len(rows) == 3
        uid = db.query(User).one().id
        old = utcnow() - timedelta(days=400)
        db.add(Audit(user_id=uid, action="ancient", at=old))
        db.add(Session(id="s" * 64, user_id=uid, csrf="x", created_at=old, seen_at=old))
        db.add(ApiKey(user_id=uid, name="gone", prefix="deadbeef", hash="h", revoked_at=old))
        db.add(ApiKey(user_id=uid, name="live", prefix="cafecafe", hash="h"))
        db.commit()
        assert retention.housekeeping(force=True)
        assert not db.query(Audit).filter_by(action="ancient").count()
        assert db.get(Session, "s" * 64) is None
        assert [k.name for k in db.query(ApiKey)] == ["live"]
        assert db.query(Session).count() == 1  # the test's own session is fresh


def test_deleting_the_account_deletes_its_audit_rows(op, monkeypatch):
    app, c = op
    from mason.server import oauth
    from mason.server.models import Audit, db
    monkeypatch.setattr(oauth, "_http", lambda *a, **kw: (204, {}))
    c.put("/api/v1/settings", json={"orgs": ["acme"]}, headers=H)
    assert c.delete("/api/v1/me", headers=H).status_code == 200
    with app.app_context():
        rows = db.query(Audit).all()
        assert [(r.action, r.user_id, r.ip, r.ok) for r in rows] == [("account.delete", None, "", True)]
        assert "Mike" not in rows[0].detail + rows[0].target


# ── rate limits and storage caps ─────────────────────────────────────────────


def test_api_rate_limits(op, monkeypatch):
    app, c = op
    s = app.config["OP"]
    monkeypatch.setattr(s, "rate_writes", 2)
    assert [c.put("/api/v1/settings", json={"orgs": ["a"]}, headers=H).status_code for _ in range(3)] == [200, 200, 429]
    r = c.put("/api/v1/settings", json={"orgs": ["a"]}, headers=H)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1 and "wait" in r.json["error"]
    assert c.get("/api/v1/me").status_code == 200  # reads have their own, larger budget
    monkeypatch.setattr(s, "rate_anon", 2)
    anon = app.test_client()
    assert [anon.get("/api/v1/me").status_code for _ in range(3)] == [200, 200, 429]
    assert anon.get("/api/v1/health").status_code == 200  # health checks are never limited


def _sign_in_as(app, login, uid):
    from mason.server import oauth
    with app.test_request_context("/api/v1/auth/github/device/poll", environ_base={"REMOTE_ADDR": "198.51.100.1"}):
        oauth.profile = lambda name, token: {"uid": uid, "login": login, "name": login, "avatar_url": ""}
        return oauth.sign_in("github", {"access_token": f"t-{login}"}, None)


def test_new_accounts_are_limited(op, monkeypatch):
    app, _ = op
    from mason.server import oauth
    monkeypatch.setattr(oauth, "profile", oauth.profile)  # restored after the test
    s = app.config["OP"]
    monkeypatch.setattr(s, "allow_users", set())  # open sign-in
    monkeypatch.setattr(s, "max_users", 2)
    assert _sign_in_as(app, "ann", "2").name == "ann"
    with pytest.raises(PermissionError, match="as many accounts"):
        _sign_in_as(app, "bob", "3")
    assert _sign_in_as(app, "ann", "2").name == "ann"  # existing accounts still sign in
    with pytest.raises(PermissionError, match="as many accounts"):
        _sign_in_as(app, "root", "4")
    monkeypatch.setattr(s, "allow_users", {("github", "root"), ("github", "ann")})
    assert _sign_in_as(app, "root", "4").name == "root"  # MASON_ALLOW_USERS always get in
    monkeypatch.setattr(s, "allow_users", set())
    monkeypatch.setattr(s, "max_users", 0)
    monkeypatch.setattr(s, "signups_per_ip", 1)
    from mason.server import limits
    limits.reset()
    assert _sign_in_as(app, "cid", "5").name == "cid"
    with pytest.raises(PermissionError, match="from this address"):
        _sign_in_as(app, "dan", "6")


def test_key_and_session_caps(op, monkeypatch):
    app, c = op
    from mason.server import auth
    from mason.server.models import ApiKey, Session, User, db
    monkeypatch.setattr(app.config["OP"], "max_keys", 2)
    made = [c.post("/api/v1/keys", json={"name": f"k{n}"}, headers=H) for n in range(3)]
    assert [r.status_code for r in made] == [201, 201, 409] and "revoke one first" in made[2].json["error"]
    for _ in range(6):  # revoked keys don't count, and only a few of them are kept
        assert c.delete(f"/api/v1/keys/{made[0].json['id']}", headers=H).status_code == 200
        made[0] = c.post("/api/v1/keys", json={"name": "again"}, headers=H)
        assert made[0].status_code == 201
    with app.app_context():
        assert db.query(ApiKey).count() <= 4
    assert c.post("/api/v1/keys", json={"days": True}, headers=H).status_code == 400
    monkeypatch.setattr(app.config["OP"], "max_sessions", 2)
    with app.test_request_context("/"):
        user = db.query(User).one()
        for _ in range(3):
            auth.start_session(app.make_response("x"), user)
        assert db.query(Session).filter_by(user_id=user.id).count() == 2


def test_settings_size_and_device_lookups(op):
    app, c = op
    big = [f"org-{n:03d}-" + "x" * 180 for n in range(200)]
    r = c.put("/api/v1/settings", json={"orgs": big, "repos": big}, headers=H)
    assert r.status_code == 413 and "KB" in r.json["error"]
    codes = [c.get("/api/v1/device/info?code=BCDF-GHJK").status_code for _ in range(11)]
    assert codes[:10] == [404] * 10 and codes[10] == 429
    assert c.post("/api/v1/device/approve", json={"user_code": "X", "days": 99999}, headers=H).status_code == 429


def test_proxy_reads_dont_count(op, monkeypatch):
    app, c = op
    monkeypatch.setattr(app.config["OP"], "rate_api", 2)
    codes = [c.get("/api/v1/github/rest/repos/acme/web").status_code for _ in range(4)]
    assert 429 not in codes
    assert [c.get("/api/v1/me").status_code for _ in range(3)] == [200, 200, 429]


def test_old_databases_lose_is_admin(tmp_path):
    import sqlite3

    from mason.server import models
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(200), avatar_url VARCHAR(500), "
                      "created_at DATETIME, last_login DATETIME, settings_json TEXT, is_admin BOOLEAN NOT NULL);"
                      "INSERT INTO users VALUES (1, 'Mike', '', NULL, NULL, '{}', 1);"
                      "CREATE TABLE instance_settings (key VARCHAR(100) PRIMARY KEY, value TEXT);")
    con.close()
    engine = models.init(f"sqlite:///{path}")
    from sqlalchemy import inspect
    have = inspect(engine)
    assert "is_admin" not in {c["name"] for c in have.get_columns("users")}
    assert have.has_table("operators")
    models.db.add(models.User(name="Ann"))  # would fail on the old NOT NULL is_admin
    models.db.commit()
    assert [u.name for u in models.db.query(models.User).order_by(models.User.id)] == ["Mike", "Ann"]
    models.db.remove()
    engine.dispose()


def test_plugins_off_for_everyone(monkeypatch):
    from importlib import metadata

    from mason import plugins

    class EP:
        def __init__(self, name):
            self.name = name
        def load(self):  # noqa: E301
            return type("P", (), {"name": self.name})

    class EPs:
        def select(self, group):
            return [EP("a"), EP("b")]
    monkeypatch.setattr(metadata, "entry_points", lambda: EPs())
    monkeypatch.setattr(plugins, "_plugins", None)
    monkeypatch.delenv("MASON_PLUGINS", raising=False)
    monkeypatch.setenv("MASON_PLUGINS_OFF", "b")
    assert [p.name for p in plugins.load()] == ["a"]  # b never loads, for anyone


def test_serve_users(op, capsys):
    app, c = op
    from mason.server.app import print_users
    c.post("/api/v1/keys", json={"name": "k"}, headers=H)
    print_users(app.config["OP"])
    out = capsys.readouterr().out
    assert out.startswith("1 users, 1 sessions, 1 working API keys")
    assert "Mike" in out and "github:mike" in out and "1 keys" in out


def test_operator_panel(op, monkeypatch, capsys):
    app, mike = op
    from mason import plugins
    from mason.server import operators, security
    from mason.server.app import operator_command
    from mason.server.models import Identity, Session, User, db

    class Demo:
        name = "demo"
        def tui_tabs(self): return []  # noqa: E301,E704
        def tui_checks(self): return []  # noqa: E301,E704
        def status_columns(self): return []  # noqa: E301,E704
    monkeypatch.setattr(plugins, "load", lambda: [Demo()])
    with app.app_context():  # Ann, a second user with a browser session
        ann = User(name="Ann")
        db.add(ann)
        db.flush()
        db.add(Identity(user_id=ann.id, provider="github", host="github.com", uid="2", login="ann"))
        db.add(Session(id=security.sha256("ann-cookie"), user_id=ann.id, csrf="ann-csrf"))
        db.commit()
        ann_id = ann.id
    annc = app.test_client()
    annc.set_cookie("mason_session", "ann-cookie", domain="localhost")
    AH = {"X-CSRF-Token": "ann-csrf"}
    # nobody is an operator until `mason operator add`, and the web can't make one
    assert mike.get("/api/v1/operator").status_code == 403
    assert not mike.get("/api/v1/me").json["user"]["is_operator"]
    assert operator_command(app.config["OP"], "add", "github:nobody") == 1
    assert "no user signed in as github:nobody" in capsys.readouterr().err
    with app.app_context():
        assert "is an operator now" in operators.add("github:MIKE", note="runs it")
        assert "already" in operators.add("#1")
        assert operators.listing()[0].startswith("#1  Mike  github:mike")
    assert mike.get("/api/v1/me").json["user"]["is_operator"]
    got = mike.get("/api/v1/operator").json
    assert [u["name"] for u in got["users"]] == ["Mike", "Ann"] and got["users"][0]["operator"]
    assert got["stats"]["users"] == 2 and got["plugins"] == [{"name": "demo", "description": "", "off_for_all": False}]
    assert annc.get("/api/v1/operator").status_code == 403  # Ann isn't one
    key = mike.post("/api/v1/keys", json={"name": "k", "scope": "write"}, headers=H).json["key"]
    keyed = app.test_client()
    assert keyed.get("/api/v1/operator", headers={"Authorization": f"Bearer {key}"}).status_code == 403  # browser only
    # a plugin off for everyone is off for Ann too, and she can't switch it back on for herself
    assert mike.put("/api/v1/operator/plugins/demo", json={"on": False}, headers=H).json["plugins"][0]["off_for_all"]
    demo = annc.get("/api/v1/plugins").json["plugins"][0]
    assert not demo["on"] and demo["off_for_all"]
    annc.put("/api/v1/plugins/demo", json={"on": True}, headers=AH)
    assert not annc.get("/api/v1/plugins").json["plugins"][0]["on"]
    assert annc.put("/api/v1/operator/plugins/demo", json={"on": True}, headers=AH).status_code == 403
    mike.put("/api/v1/operator/plugins/demo", json={"on": True}, headers=H)
    assert annc.get("/api/v1/plugins").json["plugins"][0]["on"]
    # operators see accounts, never another user's settings, and can sign a user out everywhere
    assert "settings" not in got["users"][1] and "defaults" not in str(got["users"])
    assert mike.post(f"/api/v1/operator/users/{ann_id}/sign-out", headers=H).json == {"sessions": 1, "keys": 0}
    assert annc.get("/api/v1/me").json["user"] is None
    assert {a["action"] for a in mike.get("/api/v1/audit").json} >= {"operator.plugin", "operator.sign-out"}
    # removing takes effect at the next request
    assert operator_command(app.config["OP"], "remove", "mike") == 0
    assert mike.get("/api/v1/operator").status_code == 403


def test_tag_only(op):
    _, c = op
    assert c.get("/api/v1/release?repo=acme/plugin-b").json["publishes"] == ".github/workflows/release.yml"
    assert c.get("/api/v1/release?repo=acme/api").json["publishes"] == ""
    got = c.post("/api/v1/tags", json={"repo": "acme/plugin-b", "tag": "v0.4.0", "dry_run": True}, headers=H).json
    assert "[dry-run] push the tag v0.4.0 on main of acme/plugin-b; .github/workflows/release.yml" in got["text"]
    got = c.post("/api/v1/tags", json={"repo": "acme/plugin-a", "tag": "v1.0.0"}, headers=H).json
    assert not got["ok"] and "has a tag v1.0.0 already" in got["text"]
    assert c.post("/api/v1/tags", json={"repo": "acme/api", "tag": ""}, headers=H).status_code == 400
    assert any(a["action"] == "tag.create" for a in c.get("/api/v1/audit").json)
    got = c.post("/api/v1/tags", json={"repo": "acme/plugin-b", "tag": "v9.0.0", "dry_run": True}, headers=H).json
    assert "plugin.yaml says 0.4.0" in got["text"]
    page = c.get("/api/v1/release?repo=acme/plugin-b").json
    assert page["version"]["tag"] == "v0.4.0" and page["version"]["todo"]
    assert c.get("/api/v1/release?repo=acme/api").json["version"] is None


def test_bump_and_release_from_the_web(op):
    _, c = op
    got = c.post("/api/v1/bump", json={"repo": "acme/plugin-a", "level": "minor", "dry_run": True}, headers=H).json
    assert "1.0.0 -> 1.1.0" in got["text"] and "[dry-run] branch bump/v1.1.0" in got["text"]
    assert c.post("/api/v1/bump", json={"repo": "acme/plugin-a", "level": "huge"}, headers=H).status_code == 400
    got = c.post("/api/v1/release/version", json={"repo": "acme/plugin-b", "dry_run": True}, headers=H).json
    assert "[dry-run] push tag v0.4.0 on main" in got["text"]
    assert c.post("/api/v1/bump", json={"repo": "gitlab:g/p"}, headers=H).status_code == 400
