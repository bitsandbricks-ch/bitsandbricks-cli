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
    for key in ("PATH", "FAKE_GH_LOG", "XDG_CONFIG_HOME", "BNB_OPEN", "BNB_PLUGINS"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.delenv("BNB_DEFAULTS", raising=False)
    from mortar import config, transport
    from mortar.server import oauth, security
    from mortar.server import settings as S
    from mortar.server.app import create_app
    from mortar.server.models import Identity, Session, User, db

    settings = S.Settings(
        url="http://localhost:8080", data=tmp_path, database=f"sqlite:///{tmp_path / 'op.db'}", secret_key="k" * 40,
        dev=True, github=S.Provider("github", "id", "secret", "https://github.com", "https://api.github.com", "repo"),
        gitlab=S.Provider("gitlab", "", "", "https://gitlab.com", "https://gitlab.com/api/v4", "api"),
        allow_users={("github", "mike")}, plugin_index=str(tmp_path / "index.json"))
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
    client.set_cookie("bnbo_session", raw, domain="localhost")
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
    assert "state=" in r.headers["Location"] and "bnbo_oauth=" in r.headers["Set-Cookie"]
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
    assert "update LICENSE" in text and "[dry-run] PR on bnb/sync-" in text
    text = c.post("/api/v1/new", json={"repo": "acme/plugin-c", "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] create private acme/plugin-c from the template acme/plugin-tpl" in text
    text = c.post("/api/v1/approve", json={"repo": "acme/api", "id": 5, "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] approve production for acme/api main" in text
    wf = {w["repo"]: w for w in c.get("/api/v1/workflows").json["workflows"]}
    assert wf["acme/api"] == {"repo": "acme/api", "workflows": 0, "findings": []}
    assert {f["kind"] for f in wf["acme/web"]["findings"]} == {"pinned", "permissions", "pull_request_target",
                                                              "injection"}
    text = c.post("/api/v1/workflows/pin", json={"repos": ["web"], "dry_run": True}, headers=H).json["text"]
    assert "[dry-run] PUT /repos/acme/web/contents/.github/workflows/ci.yml" in text
    log = c.get("/api/v1/audit").json
    assert {"merge", "doctor.fix", "sync", "new", "approve", "workflows.pin"} <= {a["action"] for a in log}
    assert all(a["dry_run"] for a in log if a["action"] in ("merge", "sync"))


def test_console(op):
    _, c = op
    out = c.post("/api/v1/console", json={"line": "bnb status"}, headers=H).json["text"]
    assert "acme/api" in out and "v1.10.0" in out
    assert "draft, left open" in c.post("/api/v1/console", json={"line": "bnb merge --dry-run web#8"},
                                        headers=H).json["text"]
    assert c.post("/api/v1/console", json={"line": "bnb init /etc"}, headers=H).status_code == 400
    assert c.post("/api/v1/console", json={"line": "rm -rf /"}, headers=H).status_code == 400
    assert "usage" in c.post("/api/v1/console", json={"line": "bnb status --nope"}, headers=H).json["text"]


def test_api_keys_and_device_login(op):
    app, c = op
    r = c.post("/api/v1/keys", json={"name": "laptop", "scope": "read"}, headers=H)
    key = r.json["key"]
    assert key.startswith("bnbo_") and r.status_code == 201
    k = app.test_client()
    auth = {"Authorization": f"Bearer {key}"}
    assert k.get("/api/v1/me", headers=auth).json["scope"] == "read"
    assert k.post("/api/v1/merge", json={"targets": ["web#7"]}, headers=auth).status_code == 403  # read key
    assert k.post("/api/v1/merge", json={"targets": ["web#7"], "dry_run": True}, headers=auth).status_code == 200
    assert k.post("/api/v1/keys", json={"name": "x"}, headers=auth).status_code == 403  # keys come from the browser
    assert k.get("/api/v1/me", headers={"Authorization": "Bearer bnbo_deadbeef_nope"}).status_code == 401
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
    assert got["key"].startswith("bnbo_") and got["scope"] == "write"
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
    assert d["repo"] == "acme/defaults" and "bitsandbricks.toml" in d["files"] and d["path"] == "bitsandbricks.toml"
    assert d["problems"] == []
    bad = c.post("/api/v1/defaults/validate", json={"path": "bitsandbricks.toml",
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


def test_old_setting_names(monkeypatch):
    from mortar.server import settings as S

    monkeypatch.delenv("MORTAR_IDLE_HOURS", raising=False)
    monkeypatch.setenv("BNB_OPERATOR_IDLE_HOURS", "3")
    assert S._env("MORTAR_IDLE_HOURS") == "3"
    monkeypatch.setenv("MORTAR_IDLE_HOURS", "5")
    assert S._env("MORTAR_IDLE_HOURS") == "5"
