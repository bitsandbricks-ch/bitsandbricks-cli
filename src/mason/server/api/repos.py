"""Every mason command for GitHub and GitLab, as JSON for the web UI (and anything else with an API key).

GitHub repos are `owner/name`, GitLab projects `gitlab:group/project`; lists hold both. Reads run
mason's own code (repos.py, packages.py, defaults.py) with the user's token; changes are recorded in
the audit log and take `dry_run`.
"""

from __future__ import annotations

from flask import Blueprint, abort, g, jsonify, request

from mason import core, plugins
from mason import defaults as D
from mason import issues as I
from mason import packages as P
from mason import releases as REL
from mason import repos as R
from mason import workflows as W
from mason.server import gitlab as GL
from mason.server.api.account import body
from mason.server.auth import audit, can_write, need
from mason.server.context import as_user, gitlab_projects, load_config, valid_repo

bp = Blueprint("repos", __name__, url_prefix="/api/v1")


def is_gl(repo: str) -> bool:
    return repo.startswith(GL.PREFIX)


def wants(provider: str) -> tuple[bool, bool]:
    p = (request.args.get("provider") or provider or "all").lower()
    return p in ("all", "github"), p in ("all", "gitlab")


def github_ok(gh) -> bool:
    return getattr(gh, "name", "") != "none"


def all_repos(cfg, gh, gl, names: list[str] | None = None, group: str | None = None,
              provider: str = "all") -> tuple[list[str], list[str], dict]:
    """(GitHub repos, GitLab projects, {repo: [PR numbers]}) a request is about: the named ones, else all."""
    want_gh, want_gl = wants(provider)
    pick: dict[str, list[int]] = {}
    if names:
        gh_names, gl_names = [], []
        for n in names:
            n = str(n).strip()
            if is_gl(n):
                repo, _, num = n.partition("#")
                repo = valid_repo(repo)
                gl_names.append(repo)
                if num.isdigit():
                    pick.setdefault(repo, []).append(int(num))
            else:
                gh_names.append(n)
        found: list[str] = []
        if gh_names and github_ok(gh):
            try:
                found, more = R.pick_repos(cfg, gh_names, group)
            except RuntimeError as err:
                abort(404, str(err))
            pick.update(more)
        return found, list(dict.fromkeys(gl_names)), pick
    gh_repos: list[str] = []
    if want_gh and github_ok(gh) and cfg.orgs + cfg.repos:
        gh_repos = [r for r, i in R.listing(cfg).items() if not i.get("missing")]
        if group:
            gh_repos = [r for r in gh_repos if (gr := R.group_of(cfg, r)) and gr.name == group]
    gl_repos: list[str] = []
    if want_gl and gl is not None:
        gl_repos = [r for r, i in gitlab_projects(gl, cfg).items() if not i.get("missing")]
        if group:
            gl_repos = [r for r in gl_repos if (gr := cfg.group_of(r)) and gr.name == group]
    return gh_repos, gl_repos, pick


def names_arg() -> list[str]:
    raw = request.args.getlist("repo") or [x for x in (request.args.get("repos") or "").split(",") if x.strip()]
    return [x.strip() for x in raw if x.strip()][:500]


def write_allowed(dry: bool) -> bool:
    """Writes need a write key or a browser session; a read key only ever gets a dry run."""
    if not dry and not can_write():
        abort(403, "this API key is read-only: add dry_run, or use a write key")
    return dry


def capture_rate(fn, *args) -> str:
    try:
        return core.capture(fn, *args)
    except core.RateLimited as err:
        abort(429, str(err))


# ── lists ────────────────────────────────────────────────────────────────────


@bp.get("/repos")
@need("read")
def repos_list():
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, group=request.args.get("group"))
        out = []
        for r in gh_r:
            grp = R.group_of(cfg, r)
            out.append({"repo": r, "provider": "github", "group": grp.name if grp else "", "url": f"{gh.web}/{r}",
                        "alias": grp.alias(r) if grp else core.short(r),
                        "topics": (R.listing(cfg).get(r) or {}).get("topics") or []})
        projects = gitlab_projects(gl, cfg) if gl else {}
        for r in gl_r:
            grp = cfg.group_of(r, projects.get(r, {}).get("topics"))
            out.append({"repo": r, "provider": "gitlab", "group": grp.name if grp else "",
                        "url": projects.get(r, {}).get("url", ""),
                        "alias": grp.alias(r) if grp else r.rsplit("/", 1)[-1],
                        "topics": projects.get(r, {}).get("topics") or []})
        return jsonify({"repos": out, "groups": [x.name for x in cfg.groups], "orgs": cfg.orgs,
                        "defaults": str(cfg.source) if cfg.source else ""})


@bp.get("/status")
@need("read")
def status():
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            head = R.status_head()
            rows = [{"repo": r[0], "provider": "github", "cells": r[1:]} for r in R.status_rows(cfg, gh_r)]
        except core.RateLimited as err:
            abort(429, str(err))
        extra = len(head) - 5
        for row in (GL.status_rows(gl, cfg, gl_r) if gl and gl_r else []):
            rows.append({"repo": row["repo"], "provider": "gitlab",
                         "cells": [row["group"], row["released"], f"{row['ready']}/{row['drafts']}", row["ci"],
                                   *([""] * extra)]})
        return jsonify({"head": head[1:], "rows": rows})


@bp.get("/repo")
@need("read")
def repo_detail():
    repo = valid_repo(request.args.get("repo", ""))
    with as_user() as (gh, gl):
        cfg = load_config()
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            node = GL.nodes(gl, [repo]).get(repo) or {}
            grp = cfg.group_of(repo)
            lines = [repo, node.get("webUrl", ""), "", f"group        {grp.name if grp else '-'}"]
            rels = [r["tagName"] for r in (node.get("releases") or {}).get("nodes") or []]
            lines.append(f"released     {core.newest(rels) or '-'}")
            mrs = (node.get("mergeRequests") or {}).get("nodes") or []
            lines += ["", f"open merge requests ({len(mrs)})"] + [f"  !{m['iid']} {m['title']}  {m['webUrl']}"
                                                                    for m in mrs]
            return jsonify({"text": "\n".join(lines)})
        node = R.nodes([repo]).get(repo) or {}
        grp = R.group_of(cfg, repo)
        last = core.newest([r["tagName"] for r in (node.get("releases") or {}).get("nodes") or []])
        lines = [repo, f"{gh.web}/{repo}", "", f"group        {grp.name if grp else '-'}",
                 f"released     {last or '-'}" + (f"  {gh.web}/{repo}/releases/tag/{last}" if last else "")]
        target = ((node.get("defaultBranchRef") or {}).get("target") or {})
        lines.append(f"CI           {((target.get('statusCheckRollup') or {}).get('state') or '-').lower()}")
        for line in plugins.hook("repo_detail", repo, node):
            lines.append(line)
        prs = (node.get("pullRequests") or {}).get("nodes") or []
        lines += ["", f"open pull requests ({len(prs)})"] + [f"  #{p['number']} {p['title']}  {p['url']}" for p in prs]
        merged = (node.get("merged") or {}).get("nodes") or []
        if merged:
            lines += ["", "recently merged"] + [f"  #{p['number']} {p['title']} ({core.ago(p.get('mergedAt'))})"
                                                for p in merged]
        lines += ["", f"Actions  {gh.web}/{repo}/actions"]
        return jsonify({"text": "\n".join(lines)})


@bp.get("/prs")
@need("read")
def prs():
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        out, merged = [], []
        try:
            info = R.nodes(gh_r) if gh_r else {}
        except core.RateLimited as err:
            abort(429, str(err))
        for repo in gh_r:
            node = info.get(repo) or {}
            for p in (node.get("pullRequests") or {}).get("nodes") or []:
                commit = ((p.get("commits") or {}).get("nodes") or [{}])[0].get("commit") or {}
                out.append({"repo": repo, "provider": "github", "number": p["number"], "title": p["title"],
                            "url": p["url"], "draft": p["isDraft"], "conflicts": p.get("mergeable") == "CONFLICTING",
                            "checks": R.checks_word(commit.get("statusCheckRollup")),
                            "review": (p.get("reviewDecision") or "").lower().replace("_", " "),
                            "auto": bool(p.get("autoMergeRequest")), "author": (p.get("author") or {}).get("login", ""),
                            "branch": p.get("headRefName", "")})
            for p in (node.get("merged") or {}).get("nodes") or []:
                merged.append({"repo": repo, "provider": "github", "number": p["number"], "title": p["title"],
                               "url": p["url"], "merged_at": p.get("mergedAt"),
                               "by": (p.get("mergedBy") or {}).get("login", "")})
        if gl and gl_r:
            out += GL.mr_rows(gl, gl_r)
        return jsonify({"prs": out, "merged": merged})


@bp.get("/pr")
@need("read")
def pr():
    repo = valid_repo(request.args.get("repo", ""))
    number = request.args.get("number", type=int)
    if not number:
        abort(400, "number: the PR or MR number")
    diff = request.args.get("diff") == "1"
    with as_user() as (_gh, gl):
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            return jsonify({"text": capture_rate(GL.mr_detail, gl, repo, number, diff)})
        return jsonify({"text": capture_rate(R.pr_detail, repo, number, diff, request.args.get("body") == "1")})


@bp.post("/merge")
@need("read")
def merge():
    data = body()
    dry = write_allowed(bool(data.get("dry_run")))
    targets = [str(t) for t in (data.get("targets") or [])][:200]
    method = data.get("method") or "squash"
    if method not in ("squash", "merge", "rebase"):
        abort(400, "method: squash, merge or rebase")
    with as_user(dry) as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, pick = all_repos(cfg, gh, gl, targets, data.get("group"))
        if not targets and not data.get("all"):
            abort(400, "name the PRs (targets), or send all: true to auto-merge every ready PR")
        ctx = {"pick": pick, "now": bool(data.get("now")), "ready": bool(data.get("ready")), "method": method}
        text = capture_rate(core.for_each, gh_r, R.merge, ctx) if gh_r else ""
        for repo in gl_r:
            text += f"== {repo}\n" + capture_rate(GL.merge, gl, repo, ctx)
        R._nodes.clear()
        if gl:
            gl.caches.pop("gl_nodes", None)
    audit("merge-now" if ctx["now"] else "merge", ", ".join(targets) or "all ready", dry, detail=text)
    return jsonify({"text": text})


@bp.post("/ready")
@need("read")
def ready():
    data = body()
    dry = write_allowed(bool(data.get("dry_run")))
    repo, number = valid_repo(str(data.get("repo", ""))), int(data.get("number") or 0)
    with as_user(dry) as (_gh, gl):
        if is_gl(repo):
            text = capture_rate(GL.mark_ready, gl, repo, number)
        else:
            def go():
                if core.dry_run():
                    core.say(f"[dry-run] mark {repo}#{number} ready for review")
                else:
                    core.transport().pr_ready(repo, number)
                    core.say(f"{repo}#{number} is ready for review")
            text = capture_rate(go)
        R._nodes.clear()
    audit("ready", f"{repo}#{number}", dry, detail=text)
    return jsonify({"text": text})


# ── runs and approvals ───────────────────────────────────────────────────────


def gh_runs(repos: list[str], limit: int, failed: bool) -> list[dict]:
    def fetch(repo: str) -> list[dict]:
        q = f"per_page={limit}" + ("&status=failure" if failed else "")
        try:
            items = (core.api("GET", f"/repos/{repo}/actions/runs?{q}") or {}).get("workflow_runs") or []
        except core.RateLimited:
            raise
        except RuntimeError as err:
            return [{"repo": repo, "provider": "github", "error": str(err)}]
        return [{"repo": repo, "provider": "github", "id": r["id"], "name": r.get("name"), "event": r.get("event"),
                 "branch": r.get("head_branch"), "created": r.get("created_at"), "url": r.get("html_url"),
                 "state": "waiting" if r.get("status") == "waiting" else (r.get("conclusion") or r.get("status"))}
                for r in items]
    return [r for rows in core.pmap(fetch, repos) for r in rows]


@bp.get("/runs")
@need("read")
def runs():
    limit = max(1, min(request.args.get("limit", 3, type=int), 30))
    failed = request.args.get("failed") == "1"
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            rows = gh_runs(gh_r, limit, failed) + (GL.runs(gl, gl_r, limit, failed) if gl and gl_r else [])
        except core.RateLimited as err:
            abort(429, str(err))
        return jsonify({"runs": rows})


@bp.get("/approvals")
@need("read")
def approvals():
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            found = R.all_waiting(gh_r) if gh_r else []
            rows = [{"repo": repo, "provider": "github", "kind": "run", "id": run["id"], "name": run.get("name"),
                     "branch": run.get("head_branch"), "created": run.get("created_at"), "url": run.get("html_url"),
                     "waits_for": ", ".join(e["name"] for e in envs),
                     "blocked": "; ".join(e["blocked"] for e in envs if e.get("blocked")) if R.is_blocked(envs) else ""}
                    for repo, run, envs in found]
            rows += GL.waiting(gl, gl_r) if gl and gl_r else []
        except core.RateLimited as err:
            abort(429, str(err))
        return jsonify({"approvals": rows})


@bp.post("/approve")
@need("read")
def approve():
    data = body()
    dry = write_allowed(bool(data.get("dry_run")))
    repo, ident = valid_repo(str(data.get("repo", ""))), int(data.get("id") or 0)
    with as_user(dry) as (_gh, gl):
        if is_gl(repo):
            text = capture_rate(GL.approve, gl, repo, str(data.get("kind") or ""), ident)
        else:
            match = [(r, run, envs) for r, run, envs in R.waiting_runs(repo) if run["id"] == ident]
            if not match:
                abort(404, "that run isn't waiting for you (any more)")
            _, run, envs = match[0]
            if R.is_blocked(envs):
                abort(403, "you can't approve it: " + "; ".join(e["blocked"] for e in envs))

            what = f"{', '.join(e['name'] for e in envs)} for {repo} {run.get('head_branch')}"

            def go():
                if core.dry_run():
                    core.say(f"[dry-run] approve {what}")
                else:
                    R.approve_run(repo, run, envs)
                    core.say(f"approved {what}")
            text = capture_rate(go)
    audit("approve", f"{repo} {ident}", dry, detail=text)
    return jsonify({"text": text})


# ── releases, packages, security ─────────────────────────────────────────────


@bp.get("/releases")
@need("read")
def releases():
    problems_only = request.args.get("problems") == "1"
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            infos = R.release_infos(cfg, gh_r) if gh_r else []
            infos += [i for i in core.pmap(lambda r: GL.release_info(gl, cfg, r), gl_r)
                      if i["release"] or i["runs"] or i["error"]] if gl and gl_r else []
        except core.RateLimited as err:
            abort(429, str(err))
        out = []
        for i in infos:
            bad = R.release_problems(i)
            if problems_only and not bad:
                continue
            row = R.release_row(i)
            out.append({"repo": i["repo"], "provider": i.get("provider", "github"), "release": row[0], "when": row[1],
                        "assets": row[2], "run": row[3], "problems": bad, "text": R.release_text(i),
                        "url": (i["release"] or {}).get("html_url", "")})
        return jsonify({"releases": out})


@bp.get("/release")
@need("read")
def release_page():
    """One repo's releases: {releases: [rows], selected, text, url, suggest} for the release page."""
    repo = valid_repo(request.args.get("repo", ""))
    tag = (request.args.get("tag") or "").strip() or None
    with as_user() as (_gh, gl):
        cfg = load_config()
        try:
            if is_gl(repo):
                if gl is None:
                    abort(409, "connect GitLab first")
                return jsonify(GL.release_page(gl, cfg, repo, tag))
            return jsonify(REL.page(cfg, repo, tag))
        except core.RateLimited as err:
            abort(429, str(err))
        except RuntimeError as err:
            abort(404 if "no release" in str(err) else 502, str(err))


@bp.post("/releases")
@need("read")
def release_create():
    """Create a release: {repo, tag, title?, notes?, draft?, prerelease?, target?, generate_notes?, dry_run?}."""
    data = body()
    repo = valid_repo(str(data.get("repo") or ""))
    tag = str(data.get("tag") or "").strip()
    try:
        REL.valid_tag(tag)
    except RuntimeError as err:
        abort(400, str(err))
    args = (repo, tag, str(data.get("title") or "")[:200], str(data.get("notes") or "")[:120000],
            bool(data.get("draft")), bool(data.get("prerelease")), str(data.get("target") or "")[:250],
            bool(data.get("generate_notes")))
    dry = write_allowed(bool(data.get("dry_run")))
    with as_user(dry) as (_gh, gl):
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            text = capture_rate(GL.create_release, gl, *args)
        else:
            text = capture_rate(REL.create, *args)
    audit("release.create", f"{repo} {tag}", dry, detail=text)
    return jsonify({"text": text, "ok": not text.lstrip().startswith("error")})


@bp.post("/tags")
@need("read")
def tag_create():
    """New tag: push only the tag {repo, tag, target?, dry_run?}; a workflow that publishes on tags does the rest."""
    data = body()
    repo = valid_repo(str(data.get("repo") or ""))
    tag = str(data.get("tag") or "").strip()
    try:
        REL.valid_tag(tag)
    except RuntimeError as err:
        abort(400, str(err))
    target = str(data.get("target") or "")[:250]
    dry = write_allowed(bool(data.get("dry_run")))
    with as_user(dry) as (_gh, gl):
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            text = capture_rate(GL.push_tag, gl, repo, tag, target)
        else:
            text = capture_rate(REL.push_tag, repo, tag, target)
    audit("tag.create", f"{repo} {tag}", dry, detail=text)
    return jsonify({"text": text, "ok": not text.lstrip().startswith("error")})


@bp.get("/releases/jobs")
@need("read")
def release_jobs():
    repo = valid_repo(request.args.get("repo", ""))
    run_id = request.args.get("run", type=int)
    with as_user():
        try:
            return jsonify({"lines": R.failed_jobs(repo, run_id)})
        except RuntimeError as err:
            abort(502, str(err))


@bp.get("/packages")
@need("read")
def packages():
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            infos, error = P.package_infos(cfg, gh_r) if gh_r else ([], "")
        except core.RateLimited as err:
            abort(429, str(err))
        out = [{"provider": "github", "name": i["name"], "owner": i["owner"], "repo": i["repo"] or "",
                "url": i["url"], "row": P.row(i), "problems": P.problems(i), "text": P.text(i)} for i in infos]
        for x in GL.registry(gl, gl_r) if gl and gl_r else []:
            out.append({"provider": "gitlab", "name": x["name"], "owner": x["repo"].split("/")[0], "repo": x["repo"],
                        "url": x["url"], "row": [x["name"], ", ".join(x["tags"][:3]) or "-", "", "", ""],
                        "problems": [], "text": f"{x['url']}\n\n{x['count']} tags: " + ", ".join(x["tags"])})
        return jsonify({"packages": out, "error": error})


@bp.get("/security")
@need("read")
def security():
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            found = list(zip(gh_r, core.pmap(R.repo_alerts, gh_r)))
            found += list(zip(gl_r, core.pmap(lambda r: GL.alerts(gl, r), gl_r))) if gl and gl_r else []
        except core.RateLimited as err:
            abort(429, str(err))
        out = []
        for repo, (alerts, unread) in found:
            out.append({"repo": repo, "provider": "gitlab" if is_gl(repo) else "github", "alerts": alerts,
                        "unread": unread})
        return jsonify({"security": out})


# ── issues ───────────────────────────────────────────────────────────────────


@bp.get("/issues")
@need("read")
def issues():
    """Issues across GitHub repos and GitLab projects: state, label (repeatable), assignee, mine=1, stale=DAYS."""
    state = request.args.get("state", "open")
    if state not in ("open", "closed", "all"):
        abort(400, "state is open, closed or all")
    labels = [x for x in request.args.getlist("label") if x.strip()][:10]
    assignee = (request.args.get("assignee") or "").strip() or None
    limit = min(request.args.get("limit", 50, type=int) or 50, 100)
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        gh_who = gl_who = assignee
        if request.args.get("mine") == "1":
            gh_who = I.me() if gh_r else None
            gl_who = (gl.api("GET", "/user") or {}).get("username") if gl and gl_r else None
        try:
            out = [r for rows in core.pmap(lambda r: I.repo_issues(r, state, labels, gh_who, limit), gh_r)
                   for r in rows]
            if gl and gl_r:
                out += [r for rows in core.pmap(lambda r: GL.issues(gl, r, state, labels, gl_who, limit), gl_r)
                        for r in rows]
        except core.RateLimited as err:
            abort(429, str(err))
    stale = request.args.get("stale", 0, type=int)
    if stale:
        out = I.stale(out, stale)
    out.sort(key=lambda r: r.get("updated") or "", reverse=True)
    return jsonify({"issues": out, "labels": sorted({lab for r in out for lab in r["labels"]})})


@bp.get("/issue")
@need("read")
def issue_detail():
    repo = valid_repo(request.args.get("repo", ""))
    number = request.args.get("number", type=int)
    if not number:
        abort(400, "number: the issue number")
    with as_user() as (_gh, gl):
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            return jsonify({"text": capture_rate(GL.issue_view, gl, repo, number)})
        return jsonify({"text": capture_rate(I.view, repo, number)})


@bp.post("/issues")
@need("read")
def issue_create():
    data = body()
    repo = valid_repo(str(data.get("repo") or ""))
    title = str(data.get("title") or "").strip()
    if not title:
        abort(400, "an issue needs a title")
    text = str(data.get("body") or "")[:60000]
    labels = [str(x) for x in data.get("labels") or [] if str(x).strip()][:20]
    assignees = [str(x) for x in data.get("assignees") or [] if str(x).strip()][:10]
    dry = write_allowed(bool(data.get("dry_run")))
    with as_user(dry) as (_gh, gl):
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            out = capture_rate(GL.create_issue, gl, repo, title, text, labels)
        else:
            out = capture_rate(I.create, repo, title, text, labels, assignees)
    audit("issue.create", f"{repo}: {title[:100]}", dry, detail=out)
    return jsonify({"text": out})


@bp.post("/issue")
@need("read")
def issue_change():
    """close, reopen or comment: {repo, number, action, text?, not_planned?}."""
    data = body()
    repo = valid_repo(str(data.get("repo") or ""))
    number = int(data.get("number") or 0)
    action = data.get("action")
    if number <= 0 or action not in ("close", "reopen", "comment"):
        abort(400, "number, and action close, reopen or comment")
    text = str(data.get("text") or "")[:60000]
    dry = write_allowed(bool(data.get("dry_run")))
    with as_user(dry) as (_gh, gl):
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            out = (capture_rate(GL.issue_comment, gl, repo, number, text) if action == "comment"
                   else capture_rate(GL.issue_state, gl, repo, number, action == "close"))
        else:
            out = (capture_rate(I.comment, repo, number, text) if action == "comment"
                   else capture_rate(I.set_state, repo, number, action == "close", bool(data.get("not_planned"))))
    audit(f"issue.{action}", f"{repo}#{number}", dry, detail=out)
    return jsonify({"text": out})


@bp.get("/workflows")
@need("read")
def workflows():
    """Workflow hardening findings for every GitHub repo (GitLab CI isn't read yet)."""
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, _gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))

        def one(repo: str) -> dict:
            want = W.want_of(cfg, repo)
            files = W.workflow_files(repo)
            return {"repo": repo, "workflows": len(files), "findings": W.findings(repo, want, files)}

        try:
            out = core.pmap(one, gh_r)
        except core.RateLimited as err:
            abort(429, str(err))
    return jsonify({"workflows": out})


@bp.post("/workflows/pin")
@need("read")
def workflows_pin():
    """A PR per repo that pins its actions to commit SHAs."""
    data = body()
    dry = write_allowed(bool(data.get("dry_run")))
    with as_user(dry) as (gh, gl):
        cfg = load_config()
        gh_r, _gl_r, _ = all_repos(cfg, gh, gl, [str(x) for x in data.get("repos") or []], data.get("group"))
        text = capture_rate(W.workflows, cfg, gh_r, True) if gh_r else "no GitHub repos"
    audit("workflows.pin", ", ".join(data.get("repos") or []) or "all", dry, detail=text)
    return jsonify({"text": text})


# ── doctor, sync, new ────────────────────────────────────────────────────────


@bp.post("/doctor")
@need("read")
def doctor():
    data = body()
    fix = bool(data.get("fix"))
    dry = write_allowed(bool(data.get("dry_run")) or not fix)
    with as_user(dry) as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, [str(x) for x in data.get("repos") or []], data.get("group"))
        text = capture_rate(D.doctor_all, cfg, gh_r, fix) if gh_r else ""
        for repo in gl_r:
            text += f"== {repo}\n" + capture_rate(GL.doctor, gl, cfg, repo, fix)
    if fix:
        audit("doctor.fix", ", ".join(data.get("repos") or []) or "all", dry, detail=text)
    return jsonify({"text": text})


@bp.post("/sync")
@need("read")
def sync():
    data = body()
    dry = write_allowed(bool(data.get("dry_run")))
    only = [str(x) for x in data.get("only") or []] or None
    with as_user(dry) as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, [str(x) for x in data.get("repos") or []], data.get("group"))
        text = ""
        if gh_r:
            ctx = D.sync_ctx(cfg, gh_r, only)
            text = capture_rate(core.for_each, gh_r, D.sync, ctx)
        for repo in gl_r:
            text += f"== {repo}\n" + capture_rate(GL.sync, gl, cfg, repo, only)
    audit("sync", ", ".join(data.get("repos") or []) or "all", dry, detail=text)
    return jsonify({"text": text})


@bp.post("/new")
@need("read")
def new_repo():
    data = body()
    dry = write_allowed(bool(data.get("dry_run")))
    repo = valid_repo(str(data.get("repo", "")))
    with as_user(dry) as (_gh, gl):
        cfg = load_config()
        args = (cfg, repo, data.get("group") or None, bool(data.get("public")),
                str(data.get("description") or "")[:300])
        if is_gl(repo):
            if gl is None:
                abort(409, "connect GitLab first")
            text = capture_rate(GL.create, gl, *args)
        else:
            text = capture_rate(D.create_repo, *args)
        R._listing.clear()
    audit("new", repo, dry, detail=text)
    return jsonify({"text": text})


@bp.get("/config")
@need("read")
def config_view():
    with as_user():
        cfg = load_config()
        groups = []
        for grp in cfg.groups:
            groups.append({"name": grp.name, "match": grp.match, "from": grp.reference, "files": grp.files,
                           "rulesets": grp.rulesets, "settings": grp.settings, "security": grp.security,
                           "require": grp.require, "immutable_releases": grp.immutable_releases,
                           "vulnerability_reporting": grp.vulnerability_reporting,
                           "release_assets": grp.release_assets, "gitlab": (grp.extra or {}).get("gitlab"),
                           "extra": {k: v for k, v in (grp.extra or {}).items() if k != "gitlab"}})
        return jsonify({"defaults": str(cfg.source) if cfg.source else "", "orgs": cfg.orgs, "repos": cfg.repos,
                        "exclude": cfg.exclude, "groups": groups,
                        "plugins": [p.name for p in plugins.load()]})


@bp.get("/rate")
@need("read")
def rate():
    with as_user() as (gh, gl):
        out = {"github": core.rate_limit() if github_ok(gh) else {}}
        if gl is not None:
            out["gitlab"] = gl.last_rate
        info = lambda t: t.cache_info() if hasattr(t, "cache_info") else {}  # noqa: E731
        out["cache"] = {"github": info(gh), "gitlab": info(gl) if gl else {}}
        return jsonify(out)


@bp.post("/cache/clear")
@need("read")
def cache_clear():
    with as_user() as (gh, gl):
        for t in (gh, gl):
            if t is None:
                continue
            t.caches.clear()
            if hasattr(t, "store"):
                t.store.clear()
    return jsonify({"ok": True, "user": g.user.id})
