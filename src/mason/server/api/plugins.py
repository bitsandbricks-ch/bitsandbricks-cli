"""Plugins in the web UI (their tabs, checks and actions) and the marketplace.

Plugins are Python packages installed next to the operator (mason's `mason.plugins` entry
points). Each user can switch a plugin off for themselves; an admin can switch it off for everyone.
The marketplace lists a signed plugin index; an admin may install from it only when the operator
runs with MASON_PLUGIN_INSTALL=1 (it then needs a restart).
"""

from __future__ import annotations

import contextlib
import json

from flask import Blueprint, abort, current_app, g, jsonify, request

from mason import core, market, plugins
from mason.server.api.account import body, secret_names, secret_of
from mason.server.auth import audit, can_write, need
from mason.server.context import as_user, load_config, user_settings, valid_repo
from mason.server.models import Setting, db

bp = Blueprint("plugins", __name__, url_prefix="/api/v1")


def off_for_all() -> set[str]:
    row = db.get(Setting, "plugins_off")
    try:
        return set(json.loads(row.value)) if row else set()
    except ValueError:
        return set()


def active() -> list:
    off = off_for_all() | set(user_settings(g.user).get("plugins_off") or [])
    return [p for p in plugins.load() if p.name not in off]


def plugin(name: str):
    p = next((p for p in active() if p.name == name), None)
    if p is None:
        abort(404, f"no plugin {name} (or it is switched off)")
    return p


@contextlib.contextmanager
def plugin_context(dry: bool | None = None):
    """as_user, plus the user's plugin secrets."""
    secrets = {n: secret_of(g.user, n) for n in secret_names()}
    token = plugins.SECRETS.set({k: v for k, v in secrets.items() if v})
    try:
        with as_user(dry) as both:
            yield both
    finally:
        plugins.SECRETS.reset(token)


def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception:  # a broken plugin must not break the operator
        return default


def describe(p, on: bool) -> dict:
    tabs = safe(p.tui_tabs, default=[]) if on else []
    checks = safe(p.tui_checks, default=[]) if on else []
    actions = safe(p.web_actions, default=[]) if on and hasattr(p, "web_actions") else []
    return {
        "name": p.name, "description": getattr(p, "description", ""), "on": on,
        "tabs": [{"id": t["id"], "title": t["title"], "rows": [{"key": k, "label": lab} for k, lab in t["rows"]],
                  "links": "url" in t} for t in tabs],
        "checks": [{"key": k, "label": lab} for k, lab, _ in checks],
        "actions": [{k: a.get(k) for k in ("id", "title", "help", "repo", "write", "params")} for a in actions],
        "commands": list(getattr(p, "web_commands", ()) or ()),
        "columns": [h for h, _ in (safe(p.status_columns, default=[]) if on else [])],
    }


@bp.get("/plugins")
@need("read")
def plugins_list():
    names = {p.name for p in active()}
    everyone_off = off_for_all()
    return jsonify({"plugins": [dict(describe(p, p.name in names), off_for_all=p.name in everyone_off)
                                for p in plugins.load()],
                    "broken": plugins.errors})


@bp.put("/plugins/<name>")
@need("write")
def plugin_toggle(name: str):
    if name not in {p.name for p in plugins.load()}:
        abort(404)
    data = body()
    if "everyone" in data:
        if not g.user.is_admin or g.get("session") is None:
            abort(403, "only admins (in the browser) switch plugins for everyone")
        off = off_for_all()
        off = off - {name} if data["everyone"] else off | {name}
        row = db.get(Setting, "plugins_off") or Setting(key="plugins_off")
        row.value = json.dumps(sorted(off))
        db.add(row)
        db.commit()
        audit("plugin.everyone", f"{name} {'on' if data['everyone'] else 'off'}")
    if "on" in data:
        s = g.user.settings
        off = set(s.get("plugins_off") or [])
        off = off - {name} if data["on"] else off | {name}
        s["plugins_off"] = sorted(off)
        g.user.settings = s
        db.commit()
    return plugins_list()


@bp.get("/plugins/<name>/tabs/<tab>")
@need("read")
def plugin_tab(name: str, tab: str):
    key = request.args.get("key", "")
    t = next((t for t in safe(plugin(name).tui_tabs, default=[]) if t["id"] == tab), None)
    if t is None or key not in {k for k, _ in t["rows"]}:
        abort(404)
    with plugin_context():
        try:
            text = t["text"](key)
        except core.RateLimited as err:
            abort(429, str(err))
        except Exception as err:
            text = f"error: {err}"
        url = safe(t["url"], key) if "url" in t else None
    return jsonify({"text": text, "url": url})


@bp.post("/plugins/<name>/checks/<key>")
@need("read")
def plugin_check(name: str, key: str):
    check = next((c for c in safe(plugin(name).tui_checks, default=[]) if c[0] == key), None)
    if check is None:
        abort(404)
    with plugin_context(dry=True) as (gh, gl):
        from mason.server.api.repos import all_repos
        cfg = load_config()
        gh_r, _, _ = all_repos(cfg, gh, gl, provider="github")
        try:
            text = core.capture(check[2], gh_r)
        except core.RateLimited as err:
            abort(429, str(err))
    return jsonify({"text": text})


@bp.post("/plugins/<name>/actions/<action>")
@need("read")
def plugin_action(name: str, action: str):
    a = next((a for a in safe(plugin(name).web_actions, default=[]) if a.get("id") == action), None)
    if a is None:
        abort(404)
    data = body()
    dry = bool(data.get("dry_run"))
    if a.get("write") and not dry and not can_write():
        abort(403, "this API key is read-only: add dry_run")
    repo = valid_repo(str(data.get("repo", ""))) if a.get("repo") else None
    params = {k: str(v)[:500] for k, v in (data.get("params") or {}).items()
              if k in {p.get("name") for p in a.get("params") or []}}
    for p in a.get("params") or []:
        if p.get("choices") and params.get(p["name"]) not in p["choices"]:
            abort(400, f"{p['name']}: pick one of {', '.join(p['choices'])}")
    with plugin_context(dry):
        try:
            text = core.capture(a["run"], repo, params)
        except core.RateLimited as err:
            abort(429, str(err))
    if a.get("write"):
        audit(f"plugin.{name}.{action}", repo or "", dry, detail=text)
    return jsonify({"text": text})


# ── marketplace ──────────────────────────────────────────────────────────────


@bp.get("/market")
@need("read")
def market_list():
    op = current_app.config["OP"]
    try:
        got = market.listing(op.plugin_index, op.plugin_index_keys)
    except (RuntimeError, OSError) as err:
        abort(502, f"the plugin index: {err}")
    loaded = {p.name for p in plugins.load()}
    for p in got["plugins"]:
        p["loaded"] = p["name"] in loaded
    got["can_install"] = bool(op.plugin_install and g.user.is_admin)
    got["index"] = op.plugin_index
    return jsonify(got)


@bp.post("/market/<name>/install")
@need("admin")
def market_install(name: str):
    op = current_app.config["OP"]
    if not op.plugin_install:
        abort(403, "installing from the web is off: set MASON_PLUGIN_INSTALL=1, or install with "
                   "pip next to the operator")
    if g.get("session") is None:
        abort(403, "install plugins in the browser")
    try:
        text = market.install(name, op.plugin_index, op.plugin_index_keys)
    except (RuntimeError, OSError) as err:
        audit("market.install", name, ok=False, detail=str(err))
        abort(400, str(err))
    audit("market.install", name, detail=text)
    return jsonify({"text": text, "restart": True})
