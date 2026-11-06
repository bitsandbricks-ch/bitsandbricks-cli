"""The defaults editor: groups, settings, rulesets (GitHub) and protected branches (GitLab), and the
shared files and templates, kept in the defaults repo as before.

The editor reads the repo with your token and writes through a pull request (or merge request) by
default, so changes to the defaults get reviewed like code; `mode: commit` commits straight to the
default branch for those who may. Everything is validated before it is written.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import urllib.parse

from flask import Blueprint, abort, g, jsonify, request

from mortar import config, core, templates
from mortar import defaults as D
from mortar.server import gitlab as GL
from mortar.server.api.account import body
from mortar.server.auth import audit, need
from mortar.server.context import as_user, user_settings, valid_repo

bp = Blueprint("defaults", __name__, url_prefix="/api/v1/defaults")
PATH = re.compile(r"^[A-Za-z0-9_.@ -][A-Za-z0-9_./@ -]{0,299}$")
MATCH_KEYS = {"org", "prefix", "suffix", "repos", "exclude", "topics", "provider"}
TARGETS = {"branch", "tag", "push"}
ENFORCEMENT = {"active", "evaluate", "disabled"}


def where() -> tuple[str, str | None]:
    s = user_settings(g.user)
    if not s.get("defaults"):
        abort(409, "pick a defaults repo first (Settings › Defaults repo)")
    return s["defaults"], s.get("ref") or None


def check_path(path: str) -> str:
    path = (path or "").strip()
    if not PATH.match(path) or ".." in path.split("/") or path.startswith("/") or "//" in path:
        abort(400, f"not a file path: {path[:80]!r}")
    return path


# ── validation ───────────────────────────────────────────────────────────────


def validate(path: str, text: str) -> list[str]:
    problems: list[str] = []
    if len(text) > 200_000:
        return ["the file is bigger than 200 KB"]
    if path == config.DEFAULTS_FILE:
        try:
            raw = config.parse_defaults(text)
        except RuntimeError as err:
            return [str(err)]
        for key in ("orgs", "repos", "exclude", "gitlab_groups"):
            if key in raw and not (isinstance(raw[key], list) and all(isinstance(x, str) for x in raw[key])):
                problems.append(f"{key} must be a list of names")
        groups = raw.get("group") or []
        if not isinstance(groups, list):
            return problems + ["[[group]] must be a list of tables"]
        names = set()
        for i, grp in enumerate(groups):
            label = f"group {grp.get('name') or i + 1}"
            if grp.get("name") in names:
                problems.append(f"{label}: the name is used twice")
            names.add(grp.get("name"))
            m = grp.get("match") or {}
            if not isinstance(m, dict):
                problems.append(f"{label}: match must be a table")
            elif set(m) - MATCH_KEYS:
                problems.append(f"{label}: match doesn't know {', '.join(sorted(set(m) - MATCH_KEYS))}")
            if m.get("provider") not in (None, "github", "gitlab"):
                problems.append(f"{label}: match.provider is github or gitlab")
            unknown = set(grp.get("settings") or {}) - set(D.REPO_SETTINGS)
            if unknown:
                problems.append(f"{label}: settings mtr doesn't check: {', '.join(sorted(unknown))}")
            off = set(config._list(grp.get("skip"))) - set(config.CHECKS)
            if off:
                problems.append(f"{label}: skip doesn't know {', '.join(sorted(off))} "
                                f"(checks: {', '.join(config.CHECKS)})")
            if grp.get("visibility") not in (None, "public", "private", "internal"):
                problems.append(f"{label}: visibility is public, private or internal")
            acts = grp.get("actions") or {}
            if not isinstance(acts, dict) or set(acts) - set(D.ACTIONS + D.WORKFLOW):
                problems.append(f"{label}: actions knows {', '.join(D.ACTIONS + D.WORKFLOW)}")
            if not isinstance(grp.get("labels") or [], (list, dict)):
                problems.append(f"{label}: labels is a list of names or a table of name = color")
            gl = grp.get("gitlab") or {}
            if gl and set(gl.get("settings") or {}) - set(GL.SETTINGS):
                problems.append(f"{label}: gitlab settings mtr doesn't know: "
                                f"{', '.join(sorted(set(gl['settings']) - set(GL.SETTINGS)))}")
            for b, levels in (gl.get("protected_branches") or {}).items():
                if not isinstance(levels, dict) or any(v not in GL.ACCESS for v in levels.values()):
                    problems.append(f"{label}: protected branch {b}: levels are 0, 30, 40 or 60")
            if grp.get("from") and not (config.REPO_NAME.match(grp["from"]) or config.GITLAB_NAME.match(grp["from"])):
                problems.append(f"{label}: from must be owner/name or gitlab:group/name")
            if i < len(groups) - 1 and not m:
                problems.append(f"{label} has no match, so it takes every repo: the groups after it never match")
        return problems
    if path.endswith(".json") and path.startswith("rulesets/"):
        try:
            rs = json.loads(text)
        except ValueError as err:
            return [f"not valid JSON: {err}"]
        if not isinstance(rs, dict):
            return ["a ruleset is a JSON object"]
        if not rs.get("name"):
            problems.append("a ruleset needs a name")
        if rs.get("target", "branch") not in TARGETS:
            problems.append("target is branch, tag or push")
        if rs.get("enforcement", "active") not in ENFORCEMENT:
            problems.append("enforcement is active, evaluate or disabled")
        rules = rs.get("rules")
        if not isinstance(rules, list) or not all(isinstance(r, dict) and r.get("type") for r in rules):
            problems.append("rules is a list of {\"type\": ...}")
        return problems
    if path.endswith((".yml", ".yaml")) and "\t" in text:
        problems.append("YAML doesn't allow tabs for indentation")
    return problems


# ── reading and writing the defaults repo ────────────────────────────────────


def read_tree(gl, repo: str, ref: str | None) -> list[str]:
    if repo.startswith(GL.PREFIX):
        return sorted(gl.blobs(repo, ref)) if gl else []
    t = core.api("GET", f"/repos/{repo}/git/trees/{ref or 'HEAD'}?recursive=1") or {}
    return sorted(e["path"] for e in t.get("tree") or [] if e.get("type") == "blob")


def read_file(gl, repo: str, path: str, ref: str | None) -> tuple[str | None, str | None]:
    if repo.startswith(GL.PREFIX):
        return (gl.file(repo, path, ref) if gl else None), None
    got = core.get_file(repo, path, ref)
    return (got[0], got[1]) if got else (None, None)


@bp.get("")
@need("read")
def overview():
    repo, ref = where()
    with as_user() as (_gh, gl):
        try:
            files = read_tree(gl, repo, ref)
            name = config.DEFAULTS_FILE
            text, _ = read_file(gl, repo, name, ref)
        except RuntimeError as err:
            abort(502, f"{repo}: {err}")
    return jsonify({"repo": repo, "ref": ref, "provider": "gitlab" if repo.startswith(GL.PREFIX) else "github",
                    "files": files, "path": name, "toml": text, "problems": validate(name, text) if text else
                    [f"{repo} has no {config.DEFAULTS_FILE} yet: start one from a template"]})


@bp.get("/file")
@need("read")
def get_file():
    repo, ref = where()
    path = check_path(request.args.get("path", ""))
    with as_user() as (_gh, gl):
        text, sha = read_file(gl, repo, path, ref)
    if text is None:
        abort(404, f"{path} isn't in {repo}")
    return jsonify({"path": path, "text": text, "sha": sha, "problems": validate(path, text)})


@bp.post("/validate")
@need("read")
def post_validate():
    data = body()
    path = check_path(str(data.get("path", "")))
    return jsonify({"problems": validate(path, str(data.get("text") or ""))})


@bp.get("/templates")
@need("read")
def get_templates():
    return jsonify(templates.catalog())


@bp.put("/file")
@need("write")
def put_file():
    data = body()
    repo, ref = where()
    path = check_path(str(data.get("path", "")))
    text = str(data.get("text") or "")
    delete = bool(data.get("delete"))
    mode = data.get("mode") or "pr"
    if mode not in ("pr", "commit"):
        abort(400, "mode is pr or commit")
    problems = [] if delete else validate(path, text)
    if problems and not data.get("force"):
        return jsonify({"problems": problems}), 422
    message = str(data.get("message") or f"{'Remove' if delete else 'Update'} {path} (Mortar)")[:200]
    with as_user() as (_gh, gl):
        try:
            url = (write_gitlab(gl, repo, ref, path, text, delete, mode, message) if repo.startswith(GL.PREFIX)
                   else write_github(repo, ref, path, text, delete, mode, message))
        except RuntimeError as err:
            audit("defaults.write", f"{repo}:{path}", ok=False, detail=str(err))
            abort(502, str(err))
        config._loaded.clear()
    audit("defaults.write", f"{repo}:{path} ({mode})", detail=url)
    return jsonify({"url": url, "mode": mode})


def write_github(repo: str, ref: str | None, path: str, text: str, delete: bool, mode: str, message: str) -> str:
    info = core.api("GET", f"/repos/{repo}") or {}
    base = ref or info.get("default_branch") or "main"
    got = core.get_file(repo, path, base)
    sha = got[1] if got else None
    if delete and not sha:
        raise RuntimeError(f"{path} isn't there")
    branch = base if mode == "commit" else f"mtr/defaults-{secrets.token_hex(3)}"
    if mode == "pr":
        head = core.api("GET", f"/repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
        core.api("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": head})
    q = urllib.parse.quote(path)
    if delete:
        core.api("DELETE", f"/repos/{repo}/contents/{q}", {"message": message, "sha": sha, "branch": branch})
    else:
        put = {"message": message, "branch": branch, "content": base64.b64encode(text.encode()).decode()}
        if sha:
            put["sha"] = sha
        core.api("PUT", f"/repos/{repo}/contents/{q}", put)
    if mode == "commit":
        return f"{core.transport().web}/{repo}/blob/{base}/{path}"
    return core.transport().pr_create(repo, base, branch, message,
                                      f"{message}\n\nOpened from Mortar's defaults editor by {g.user.name}.")


def write_gitlab(gl, repo: str, ref: str | None, path: str, text: str, delete: bool, mode: str, message: str) -> str:
    if gl is None:
        raise RuntimeError("connect GitLab first")
    info = gl.api("GET", gl.project(repo)) or {}
    base = ref or info.get("default_branch") or "main"
    exists = gl.file(repo, path, base) is not None
    branch = base if mode == "commit" else f"mtr/defaults-{secrets.token_hex(3)}"
    action = "delete" if delete else ("update" if exists else "create")
    commit = {"branch": branch, "commit_message": message,
              "actions": [{"action": action, "file_path": path, **({} if delete else {"content": text})}]}
    if mode == "pr":
        commit["start_branch"] = base
    gl.api("POST", f"{gl.project(repo)}/repository/commits", commit)
    if mode == "commit":
        return f"{info.get('web_url', '')}/-/blob/{base}/{path}"
    mr = gl.api("POST", f"{gl.project(repo)}/merge_requests", {
        "source_branch": branch, "target_branch": base, "title": message, "remove_source_branch": True,
        "description": f"Opened from Mortar's defaults editor by {g.user.name}."}) or {}
    return mr.get("web_url", "")


@bp.post("/ruleset-from")
@need("read")
def ruleset_from():
    """The live rulesets of a repo as defaults files: copy a repo you like into the defaults."""
    repo = valid_repo(str(body().get("repo", "")))
    if repo.startswith(GL.PREFIX):
        abort(400, "GitLab has no rulesets: use [group.gitlab] protected_branches")
    with as_user():
        try:
            live = D.live_rulesets(repo)
        except RuntimeError as err:
            abort(502, str(err))
    out = []
    for name, rs in live.items():
        clean = {k: rs[k] for k in D.RULESET_FIELDS if k in rs}
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "ruleset"
        out.append({"name": name, "path": f"rulesets/default/{slug}.json", "text": json.dumps(clean, indent=2) + "\n"})
    return jsonify(out)
