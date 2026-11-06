"""GitLab: the mason commands for GitLab projects, with the user's own GitLab token.

Projects are named `gitlab:group/subgroup/project` next to GitHub's `owner/name`, so both live in
one list. Groups in mason.toml match them too (`match = { provider = "gitlab", ... }` limits
a group to GitLab); a group's `gitlab` table says what its GitLab projects should have:

    [group.gitlab]
    settings = { only_allow_merge_if_pipeline_succeeds = true, remove_source_branch_after_merge = true }
    protected_branches = { main = { push_access_level = 0, merge_access_level = 30 } }
    approvals_before_merge = 1

A group's `files` (a folder in the defaults repo) are synced the same way as on GitHub.

Like core, output goes through say(), so the same text shows in the web console and the TUI.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import re
import urllib.parse

from mason import core
from mason.core import ago, compact, git_blob_sha, newest, say
from mason.transport import HttpError, HttpTransport

PREFIX = "gitlab:"
NAME = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$")
SETTINGS = ("only_allow_merge_if_pipeline_succeeds", "only_allow_merge_if_all_discussions_are_resolved",
            "remove_source_branch_after_merge", "squash_option", "merge_method", "issues_enabled", "wiki_enabled",
            "snippets_enabled", "packages_enabled", "container_registry_access_level", "visibility",
            "allow_merge_on_skipped_pipeline", "autoclose_referenced_issues", "printing_merge_request_link_enabled",
            "ci_forward_deployment_enabled", "merge_requests_template", "approvals_before_merge")
ACCESS = {0: "no one", 30: "developers", 40: "maintainers", 60: "admins"}


def full(repo: str) -> str:
    return repo if repo.startswith(PREFIX) else PREFIX + repo


def path_of(repo: str) -> str:
    p = repo[len(PREFIX):] if repo.startswith(PREFIX) else repo
    if not NAME.match(p):
        raise RuntimeError(f"Not a GitLab project path: {repo}")
    return p


class GitLab(HttpTransport):
    """GitLab's REST (v4) and GraphQL APIs with a token."""

    name = "gitlab"
    label = "GitLab"
    RATE_HEADERS = ("ratelimit-remaining", "ratelimit-limit", "ratelimit-reset")

    def __init__(self, token: str, api_url: str = "https://gitlab.com/api/v4", web: str = "https://gitlab.com",
                 cache: str | None = None):
        super().__init__(token, api_url=api_url, web=web, graphql_url=f"{web.rstrip('/')}/api/graphql",
                         cache=cache, user_agent="mason-server")

    def resource(self, url: str) -> str:
        return "graphql" if url == self.graphql_url else "core"

    def _scope(self, path: str) -> tuple[str, str]:
        parts = path.split("?", 1)[0].split("/")
        if path.startswith("/projects/") and len(parts) >= 3 and parts[2]:
            return "/".join(parts[:3]), full(urllib.parse.unquote(parts[2]))
        return "", ""

    def project(self, repo: str) -> str:
        return "/projects/" + urllib.parse.quote(path_of(repo), safe="")

    def pages(self, path: str, limit: int = 2000) -> list:
        found, page = [], 1
        sep = "&" if "?" in path else "?"
        while len(found) < limit:
            got = self.api("GET", f"{path}{sep}per_page=100&page={page}") or []
            found += got
            if len(got) < 100:
                break
            page += 1
        return found

    def file(self, repo: str, path: str, ref: str | None = None) -> str | None:
        q = urllib.parse.quote(path, safe="")
        try:
            got = self.api("GET", f"{self.project(repo)}/repository/files/{q}?ref={urllib.parse.quote(ref or 'HEAD')}")
        except HttpError as err:
            if err.status == 404:
                return None
            raise
        return base64.b64decode(got["content"]).decode() if got else None

    def blobs(self, repo: str, ref: str | None = None) -> dict[str, str]:
        """{path: blob sha} of every file (GitLab's tree ids are git's blob shas)."""
        q = f"?recursive=true&ref={urllib.parse.quote(ref)}" if ref else "?recursive=true"
        try:
            items = self.pages(f"{self.project(repo)}/repository/tree{q}", limit=10000)
        except HttpError as err:
            if err.status == 404:
                return {}
            raise
        return {i["path"]: i["id"] for i in items if i.get("type") == "blob"}

    def write(self, method: str, path: str, body: dict | None = None):
        if core.dry_run():
            say(f"   [dry-run] {method} {path}")
            return {}
        return self.api(method, path, body)


# ── which projects ───────────────────────────────────────────────────────────


def projects(gl: GitLab, groups: list[str], extra: list[str] | None = None, exclude: list[str] | None = None) -> dict:
    """{gitlab:path: info} of the groups' projects (subgroups too), or of your projects when no group is set."""
    found: dict[str, dict] = {}

    def add(p: dict) -> None:
        if p.get("archived"):
            return
        found[full(p["path_with_namespace"])] = {
            "topics": p.get("topics") or p.get("tag_list") or [], "url": p.get("web_url", ""),
            "default_branch": p.get("default_branch") or "main", "visibility": p.get("visibility", ""), "id": p["id"]}

    if groups:
        for got in core.pmap(lambda g: gl.pages(f"/groups/{urllib.parse.quote(g, safe='')}/projects?"
                                                 "include_subgroups=true&archived=false&with_shared=false"), groups):
            for p in got:
                add(p)
    else:
        for p in gl.pages("/projects?membership=true&archived=false&min_access_level=30&order_by=path&sort=asc"):
            add(p)
    for repo in extra or []:
        try:
            add(gl.api("GET", gl.project(repo)) or {})
        except RuntimeError:
            found[full(repo)] = {"topics": [], "missing": True}
    pats = exclude or []
    return {r: found[r] for r in sorted(found, key=str.lower)
            if not any(fnmatch.fnmatchcase(r, full(p)) or fnmatch.fnmatchcase(path_of(r), p) for p in pats)}


# ── status and merge requests: one GraphQL query per 20 projects ─────────────

FIELDS = """
  fullPath webUrl archived
  repository { rootRef tree { lastCommit { pipelines(first: 1) { nodes { status } } } } }
  releases(first: 20, sort: CREATED_DESC) { nodes { tagName } }
  mergeRequests(state: opened, first: 50, sort: UPDATED_DESC) {
    count
    nodes {
      iid title webUrl draft conflicts approved autoMergeEnabled sourceBranch detailedMergeStatus createdAt
      author { username }
      headPipeline { status }
    }
  }
"""


def nodes(gl: GitLab, repos: list[str]) -> dict[str, dict | None]:
    cache = gl.caches.space("gl_nodes")
    todo = [r for r in repos if r not in cache]

    def one(batch: list[str]) -> dict:
        parts = [f"p{i}: project(fullPath: {json.dumps(path_of(r))}) {{ ...P }}" for i, r in enumerate(batch)]
        q = "query {\n" + "\n".join(parts) + "\n}\nfragment P on Project {" + FIELDS + "}"
        try:
            data = gl.graphql(q, {})
        except RuntimeError:
            data = {}
        return {r: data.get(f"p{i}") for i, r in enumerate(batch)}

    for got in core.pmap(one, [todo[i:i + 20] for i in range(0, len(todo), 20)]):
        cache.update(got)
    return {r: cache.get(r) for r in repos}


def ci_word(status: str | None) -> str:
    s = (status or "").lower()
    return {"success": "success", "failed": "failure", "running": "pending", "pending": "pending",
            "canceled": "cancelled", "skipped": "skipped", "manual": "manual"}.get(s, s or "-")


def status_rows(gl: GitLab, cfg, repos: list[str]) -> list[dict]:
    info = nodes(gl, repos)
    rows = []
    for repo in repos:
        node = info.get(repo)
        g = cfg.group_of(repo) if cfg else None
        if not node:
            rows.append({"repo": repo, "provider": "gitlab", "group": g.name if g else "-", "released": "missing",
                         "ready": 0, "drafts": 0, "ci": "", "url": ""})
            continue
        mrs = node["mergeRequests"]
        drafts = sum(1 for m in mrs["nodes"] if m.get("draft"))
        pipes = ((((node.get("repository") or {}).get("tree") or {}).get("lastCommit") or {}).get("pipelines")
                 or {}).get("nodes") or []
        rows.append({"repo": repo, "provider": "gitlab", "group": g.name if g else "-",
                     "released": newest([r["tagName"] for r in node["releases"]["nodes"]]) or "-",
                     "ready": mrs["count"] - drafts, "drafts": drafts,
                     "ci": ci_word(pipes[0]["status"]) if pipes else "-", "url": node.get("webUrl", "")})
    return rows


def mr_rows(gl: GitLab, repos: list[str]) -> list[dict]:
    out = []
    for repo, node in nodes(gl, repos).items():
        for m in ((node or {}).get("mergeRequests") or {}).get("nodes") or []:
            out.append({"repo": repo, "provider": "gitlab", "number": int(m["iid"]), "title": m["title"],
                        "url": m["webUrl"], "draft": bool(m.get("draft")), "conflicts": bool(m.get("conflicts")),
                        "checks": ci_word((m.get("headPipeline") or {}).get("status")),
                        "review": "approved" if m.get("approved") else "",
                        "auto": bool(m.get("autoMergeEnabled")), "author": (m.get("author") or {}).get("username", ""),
                        "branch": m.get("sourceBranch", ""), "state": (m.get("detailedMergeStatus") or "").lower(),
                        "created": m.get("createdAt")})
    return out


def mr_detail(gl: GitLab, repo: str, iid: int, diff: bool = False) -> None:
    base = f"{gl.project(repo)}/merge_requests/{int(iid)}"
    m = gl.api("GET", base) or {}
    say(f"!{m.get('iid')} {m.get('title')}\n{m.get('web_url')}\n")
    say(f"  state    {m.get('state')}{' (draft)' if m.get('draft') else ''}, by "
        f"{(m.get('author') or {}).get('username', '?')}, opened {ago(m.get('created_at'))}, "
        f"updated {ago(m.get('updated_at'))}")
    say(f"  branch   {m.get('source_branch')} -> {m.get('target_branch')}")
    say(f"  merge    {m.get('detailed_merge_status') or m.get('merge_status')}"
        f"{'; auto-merge on' if m.get('merge_when_pipeline_succeeds') else ''}"
        f"{'; HAS CONFLICTS' if m.get('has_conflicts') else ''}")
    try:
        ap = gl.api("GET", f"{base}/approvals") or {}
        say(f"  review   {'approved' if ap.get('approved') else 'not approved'}"
            f" ({ap.get('approvals_left', 0)} approvals left)")
    except RuntimeError:
        pass
    if m.get("labels"):
        say(f"  labels   {', '.join(m['labels'])}")
    pipe = m.get("head_pipeline") or m.get("pipeline") or {}
    if pipe:
        say(f"\nPipeline {pipe.get('status')}: {pipe.get('web_url', '')}")
        try:
            jobs = gl.api("GET", f"{gl.project(repo)}/pipelines/{pipe['id']}/jobs?per_page=100") or []
        except RuntimeError:
            jobs = []
        for j in sorted(jobs, key=lambda j: j.get("status") == "success"):
            say(f"  {j.get('status', '?'):<10} {j.get('stage')} / {j.get('name')}")
            if j.get("status") in ("failed", "canceled"):
                say(f"             {j.get('web_url', '')}")
    try:
        commits = gl.api("GET", f"{base}/commits?per_page=15") or []
    except RuntimeError:
        commits = []
    if commits:
        say("\nCommits:")
        for c in commits[:15]:
            say(f"  {c.get('short_id')} {c.get('title')}")
    try:
        files = gl.pages(f"{base}/diffs", limit=300)
    except RuntimeError:
        files = []
    if files:
        say(f"\nFiles ({len(files)}):")
        for f in files[:40]:
            kind = "new" if f.get("new_file") else "deleted" if f.get("deleted_file") else "renamed" \
                if f.get("renamed_file") else "changed"
            say(f"  {kind:<8} {f.get('new_path')}")
    body = (m.get("description") or "").strip()
    if body:
        lines = body.splitlines()
        say("\nDescription:\n  " + "\n  ".join(lines[:25]) + (f"\n  ... {len(lines) - 25} more lines"
                                                               if len(lines) > 25 else ""))
    if diff and files:
        say("")
        for f in files:
            say(f"--- a/{f.get('old_path')}\n+++ b/{f.get('new_path')}\n{f.get('diff', '')}")


DRAFT = re.compile(r"^\s*(\[draft\]|draft:|\(draft\)|wip:|\[wip\])\s*", re.I)


def merge(gl: GitLab, repo: str, ctx: dict) -> None:
    """Auto-merge (when the pipeline succeeds) or merge now; drafts and conflicts stay open."""
    numbers = (ctx.get("pick") or {}).get(repo)
    if numbers:
        items = [gl.api("GET", f"{gl.project(repo)}/merge_requests/{int(n)}") for n in numbers]
    else:
        items = gl.pages(f"{gl.project(repo)}/merge_requests?state=opened", limit=200)
    if not items:
        say("   no open merge requests")
        return
    for m in items:
        label, link = f"!{m['iid']} {m['title']}", f"\n      {m.get('web_url', '')}"
        if m.get("state") != "opened":
            say(f"   {label}: {m.get('state')}, nothing to do{link}")
            continue
        if m.get("draft") or m.get("work_in_progress"):
            if not (numbers and ctx.get("ready")):
                say(f"   {label}: draft, left open{' (add ready to merge it anyway)' if numbers else ''}{link}")
                continue
            gl.write("PUT", f"{gl.project(repo)}/merge_requests/{m['iid']}", {"title": DRAFT.sub("", m["title"])})
            say(f"   {label}: marked ready")
        if m.get("merge_when_pipeline_succeeds") and not ctx.get("now"):
            say(f"   {label}: auto-merge already on{link}")
            continue
        if m.get("has_conflicts"):
            say(f"   {label}: has conflicts, left open{link}")
            continue
        body = {"should_remove_source_branch": True, "squash": (ctx.get("method") or "squash") == "squash"}
        if not ctx.get("now"):
            body.update({"merge_when_pipeline_succeeds": True, "auto_merge": True})
        if core.dry_run():
            say(f"   [dry-run] {'merge now' if ctx.get('now') else 'auto-merge'} {label}{link}")
            continue
        try:
            gl.api("PUT", f"{gl.project(repo)}/merge_requests/{m['iid']}/merge", body)
            done = "merged" if ctx.get("now") else "auto-merge on (merges when the pipeline passes)"
            say(f"   {label}: {done}{link}")
        except RuntimeError as err:
            say(f"   {label}: not merged: {str(err)[:200]}{link}")


def mark_ready(gl: GitLab, repo: str, iid: int) -> None:
    m = gl.api("GET", f"{gl.project(repo)}/merge_requests/{int(iid)}") or {}
    gl.write("PUT", f"{gl.project(repo)}/merge_requests/{int(iid)}", {"title": DRAFT.sub("", m.get("title", ""))})
    say(f"{full(repo)}!{iid} is ready for review")


# ── pipelines and approvals ──────────────────────────────────────────────────


def runs(gl: GitLab, repos: list[str], limit: int = 3, failed: bool = False) -> list[dict]:
    def fetch(repo: str) -> list[dict]:
        q = f"per_page={limit}" + ("&status=failed" if failed else "")
        try:
            items = gl.api("GET", f"{gl.project(repo)}/pipelines?{q}") or []
        except core.RateLimited:
            raise
        except RuntimeError as err:
            return [{"repo": repo, "provider": "gitlab", "error": str(err)}]
        return [{"repo": repo, "provider": "gitlab", "id": p["id"], "state": p.get("status"),
                 "name": f"pipeline #{p['id']}", "event": p.get("source", ""), "branch": p.get("ref", ""),
                 "created": p.get("created_at"), "url": p.get("web_url", "")} for p in items]
    return [r for rows in core.pmap(fetch, repos) for r in rows]


def waiting(gl: GitLab, repos: list[str]) -> list[dict]:
    """Deployments waiting for an approval, and manual jobs waiting to be played."""
    def fetch(repo: str) -> list[dict]:
        out = []
        try:
            for d in gl.api("GET", f"{gl.project(repo)}/deployments?status=blocked&per_page=20") or []:
                out.append({"repo": repo, "provider": "gitlab", "kind": "deployment", "id": d["id"],
                            "name": f"deploy to {(d.get('environment') or {}).get('name', '?')}",
                            "branch": d.get("ref", ""), "created": d.get("created_at"),
                            "url": (d.get("deployable") or {}).get("web_url", ""),
                            "waits_for": (d.get("environment") or {}).get("name", "")})
            for j in gl.api("GET", f"{gl.project(repo)}/jobs?scope[]=manual&per_page=20") or []:
                out.append({"repo": repo, "provider": "gitlab", "kind": "manual", "id": j["id"],
                            "name": f"{j.get('stage')} / {j.get('name')}", "branch": j.get("ref", ""),
                            "created": j.get("created_at"), "url": j.get("web_url", ""), "waits_for": "manual job"})
        except core.RateLimited:
            raise
        except RuntimeError:
            pass
        return out
    return [w for ws in core.pmap(fetch, repos) for w in ws]


def approve(gl: GitLab, repo: str, kind: str, ident: int) -> None:
    if kind == "deployment":
        gl.write("POST", f"{gl.project(repo)}/deployments/{int(ident)}/approval",
                 {"status": "approved", "comment": "Approved with mason"})
        say(f"approved deployment {ident} of {full(repo)}")
    elif kind == "manual":
        gl.write("POST", f"{gl.project(repo)}/jobs/{int(ident)}/play")
        say(f"started manual job {ident} of {full(repo)}")
    else:
        raise RuntimeError(f"unknown approval kind {kind}")


# ── releases, registry, security ─────────────────────────────────────────────


def release_info(gl: GitLab, cfg, repo: str) -> dict:
    info = {"repo": repo, "provider": "gitlab", "release": None, "missing": [], "runs": [], "error": ""}
    try:
        rels = gl.api("GET", f"{gl.project(repo)}/releases?per_page=1") or []
        tags = gl.api("GET", f"{gl.project(repo)}/pipelines?per_page=10&scope=tags") or []
    except core.RateLimited:
        raise
    except RuntimeError as err:
        info["error"] = str(err)
        return info
    if rels:
        r = rels[0]
        links = (r.get("assets") or {}).get("links") or []
        info["release"] = {"tag_name": r.get("tag_name"), "published_at": r.get("released_at") or r.get("created_at"),
                           "html_url": (r.get("_links") or {}).get("self", ""), "draft": False,
                           "prerelease": bool(r.get("upcoming_release")),
                           "assets": [{"name": a.get("name"), "url": a.get("url")} for a in links]}
        group = cfg.group_of(repo) if cfg else None
        if group:
            names = [a.get("name") or "" for a in links]
            info["missing"] = [g for g in group.release_assets if not any(fnmatch.fnmatchcase(n, g) for n in names)]
    info["runs"] = [{"name": f"pipeline #{p['id']}", "conclusion": "failure" if p.get("status") == "failed"
                     else p.get("status"), "head_branch": p.get("ref"), "created_at": p.get("created_at"),
                     "html_url": p.get("web_url")} for p in tags[:5]]
    return info


def registry(gl: GitLab, repos: list[str]) -> list[dict]:
    def fetch(repo: str) -> list[dict]:
        try:
            items = gl.api("GET", f"{gl.project(repo)}/registry/repositories?tags=true&tags_count=true") or []
        except core.RateLimited:
            raise
        except RuntimeError:
            return []
        return [{"repo": repo, "provider": "gitlab", "name": i.get("path") or i.get("name"),
                 "url": i.get("location", ""), "tags": [t.get("name") for t in (i.get("tags") or [])][:10],
                 "count": i.get("tags_count", 0)} for i in items]
    return [x for xs in core.pmap(fetch, repos) for x in xs]


VULNS = """query($p: ID!) { project(fullPath: $p) {
  vulnerabilities(state: [DETECTED, CONFIRMED], first: 100) { nodes { title severity reportType webUrl } } } }"""


def alerts(gl: GitLab, repo: str) -> tuple[list[dict], list[str]]:
    try:
        data = gl.graphql(VULNS, {"p": path_of(repo)})
    except core.RateLimited:
        raise
    except RuntimeError as err:
        return [], [f"vulnerabilities: {str(err)[:120]} (needs GitLab Ultimate and the Developer role)"]
    nodes_ = ((((data.get("project") or {}).get("vulnerabilities")) or {}).get("nodes")) or []
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}
    rows = [{"severity": (v.get("severity") or "?").lower(), "kind": (v.get("reportType") or "").lower(),
             "title": v.get("title") or "?", "where": "", "url": v.get("webUrl") or ""} for v in nodes_]
    return sorted(rows, key=lambda a: order.get(a["severity"], 5)), []


# ── issues ───────────────────────────────────────────────────────────────────

GL_STATE = {"open": "opened", "closed": "closed", "all": "all"}


def issue_row(repo: str, i: dict) -> dict:
    return {"repo": repo, "provider": "gitlab", "number": i["iid"], "title": i.get("title") or "",
            "url": i.get("web_url") or "", "state": "open" if i.get("state") == "opened" else i.get("state") or "",
            "labels": list(i.get("labels") or []), "assignees": [a["username"] for a in i.get("assignees") or []],
            "author": (i.get("author") or {}).get("username", ""), "comments": i.get("user_notes_count") or 0,
            "created": i.get("created_at"), "updated": i.get("updated_at")}


def issues(gl: GitLab, repo: str, state: str = "open", labels: list[str] | None = None,
           assignee: str | None = None, limit: int = 50) -> list[dict]:
    q = {"state": GL_STATE.get(state, "opened"), "order_by": "updated_at", "per_page": str(min(max(limit, 1), 100))}
    if labels:
        q["labels"] = ",".join(labels)
    if assignee in ("none", "*"):
        q["assignee_id"] = "None" if assignee == "none" else "Any"
    elif assignee:
        q["assignee_username"] = assignee
    got = gl.api("GET", f"{gl.project(repo)}/issues?{urllib.parse.urlencode(q)}") or []
    return [issue_row(repo, i) for i in got][:limit]


def create_issue(gl: GitLab, repo: str, title: str, body: str = "", labels: list[str] | None = None) -> str:
    if not title.strip():
        raise RuntimeError("an issue needs a title")
    data = {"title": title.strip()[:255], "description": body}
    if labels:
        data["labels"] = ",".join(labels)
    got = gl.write("POST", f"{gl.project(repo)}/issues", data) or {}
    url = got.get("web_url") or f"{path_of(repo)}/-/issues"
    say(f"{'[dry-run] ' if core.dry_run() else ''}opened {url}")
    return url


def issue_state(gl: GitLab, repo: str, iid: int, close: bool) -> None:
    gl.write("PUT", f"{gl.project(repo)}/issues/{iid}", {"state_event": "close" if close else "reopen"})
    say(f"{'closed' if close else 'reopened'} {repo}#{iid}")


def issue_comment(gl: GitLab, repo: str, iid: int, text: str) -> None:
    if not text.strip():
        raise RuntimeError("the comment is empty")
    gl.write("POST", f"{gl.project(repo)}/issues/{iid}/notes", {"body": text})
    say(f"commented on {repo}#{iid}")


def issue_view(gl: GitLab, repo: str, iid: int) -> None:
    i = gl.api("GET", f"{gl.project(repo)}/issues/{iid}") or {}
    r = issue_row(repo, i)
    say(f"#{r['number']} {r['title']}\n{r['url']}\n")
    say(f"  state     {r['state']}, by {r['author']}, opened {ago(r['created'])}, updated {ago(r['updated'])}")
    if r["labels"]:
        say(f"  labels    {', '.join(r['labels'])}")
    if r["assignees"]:
        say(f"  assignees {', '.join(r['assignees'])}")
    if (i.get("description") or "").strip():
        say("\n" + i["description"].strip()[:4000])
    notes = [n for n in gl.api("GET", f"{gl.project(repo)}/issues/{iid}/notes?per_page=20&sort=asc") or []
             if not n.get("system")]
    if notes:
        say(f"\nComments ({len(notes)}):")
        for n in notes:
            say(f"\n  {(n.get('author') or {}).get('username', '?')}, {ago(n.get('created_at'))}:")
            for line in (n.get("body") or "").strip().splitlines()[:20]:
                say(f"    {line}")


# ── doctor, sync and new projects ────────────────────────────────────────────


def want_of(gl: GitLab, cfg, group) -> dict:
    spec = (group.extra or {}).get("gitlab") or {}
    want = {"settings": dict(spec.get("settings") or {}), "protected": dict(spec.get("protected_branches") or {}),
            "label": "defaults", "files": {}, "texts": {}}
    if spec.get("approvals_before_merge") is not None:
        want["settings"]["approvals_before_merge"] = spec["approvals_before_merge"]
    ref = group.reference
    if ref and ref.startswith(PREFIX):
        live = gl.api("GET", gl.project(ref)) or {}
        base = {k: live.get(k) for k in SETTINGS if k in live and k not in ("visibility", "merge_requests_template")}
        want["settings"] = {**base, **want["settings"]}
        want["label"] = ref.rsplit("/", 1)[-1]
        if isinstance(group.files, list):
            shas = gl.blobs(ref)
            want["files"] = {p: shas[p] for p in group.files if p in shas}
            for p in want["files"]:
                want["texts"][p] = gl.file(ref, p) or ""
    texts = cfg.files_of(group)
    want["files"].update({p: git_blob_sha(t) for p, t in texts.items()})
    want["texts"].update(texts)
    return want


def doctor(gl: GitLab, cfg, repo: str, fix: bool = False) -> None:
    group = cfg.group_of(repo)
    if not group:
        say("   ok (no group)")
        return
    want = want_of(gl, cfg, group)
    live = gl.api("GET", gl.project(repo)) or {}
    problems, fixable = [], {}
    for k, v in want["settings"].items():
        if live.get(k) != v:
            problems.append(f"{k} is {compact(live.get(k))}, {want['label']} has {compact(v)}")
            fixable[k] = v
    prot = {b["name"]: b for b in gl.api("GET", f"{gl.project(repo)}/protected_branches") or []}
    bad_branches = []
    for name, levels in want["protected"].items():
        have = prot.get(name)
        if not have:
            problems.append(f"branch {name} isn't protected")
            bad_branches.append(name)
            continue
        for kind in ("push_access_level", "merge_access_level"):
            if kind not in levels:
                continue
            got = sorted({a.get("access_level") for a in have.get(kind.replace("_level", "_levels"), [])})
            if got != [levels[kind]]:
                names = ", ".join(ACCESS.get(x, str(x)) for x in got) or "nobody"
                problems.append(f"branch {name}: {kind.split('_')[0]} by {names}, {want['label']} has "
                                f"{ACCESS.get(levels[kind], levels[kind])}")
                bad_branches.append(name)
    files = gl.blobs(repo, live.get("default_branch"))
    for path, sha in want["files"].items():
        if path not in files:
            problems.append(f"{path} is missing")
        elif files[path] != sha:
            problems.append(f"{path} differs from {want['label']}")
    if fix and (fixable or bad_branches):
        if fixable:
            gl.write("PUT", gl.project(repo), fixable)
        for name in dict.fromkeys(bad_branches):
            if name in prot:
                gl.write("DELETE", f"{gl.project(repo)}/protected_branches/{urllib.parse.quote(name, safe='')}")
            gl.write("POST", f"{gl.project(repo)}/protected_branches", {"name": name, **want["protected"][name]})
        say(f"   fixed {len(fixable)} settings, {len(set(bad_branches))} protected branches")
        problems = [p for p in problems if p.endswith(("is missing", f"differs from {want['label']}"))]
    if not problems:
        say(f"   ok (group {group.name})")
        return
    say(f"   (group {group.name})")
    for p in problems:
        say(f"   - {p}")
    if not fix and (fixable or bad_branches):
        say(f"   fix: mason doctor --fix {full(repo)}")
    if any(p.endswith(("is missing", f"differs from {want['label']}")) for p in problems):
        say(f"   fix: mason sync {full(repo)}  (merge request with the files)")


def sync(gl: GitLab, cfg, repo: str, only: list[str] | None = None) -> None:
    group = cfg.group_of(repo)
    want = want_of(gl, cfg, group) if group else None
    if not want or not want["files"]:
        say("   no files to sync" + ("" if group else " (no group)"))
        return
    live = gl.api("GET", gl.project(repo)) or {}
    base = live.get("default_branch") or "main"
    ours = gl.blobs(repo, base)
    changed = {p: want["texts"][p] for p, sha in want["files"].items()
               if ours.get(p) != sha and p in want["texts"] and (not only or p in only)}
    if not changed:
        say(f"   in sync with {want['label']}")
        return
    for p in changed:
        say(f"   {'update' if p in ours else 'add'} {p}")
    stamp = core.git_blob_sha("".join(sorted(want["files"].values())))[:7]
    branch = f"mason/sync-{stamp}"
    open_mrs = gl.api("GET", f"{gl.project(repo)}/merge_requests?state=opened&source_branch={branch}") or []
    if open_mrs:
        say(f"   merge request already open: {open_mrs[0].get('web_url')}")
        return
    if core.dry_run():
        say(f"   [dry-run] merge request on {branch} with {len(changed)} file(s)")
        return
    gl.api("POST", f"{gl.project(repo)}/repository/commits", {
        "branch": branch, "start_branch": base, "commit_message": f"chore: sync shared files with {want['label']}",
        "actions": [{"action": "update" if p in ours else "create", "file_path": p, "content": t}
                    for p, t in changed.items()]})
    mr = gl.api("POST", f"{gl.project(repo)}/merge_requests", {
        "source_branch": branch, "target_branch": base, "remove_source_branch": True,
        "title": f"Sync shared files with {want['label']}",
        "description": "Copies these files from the defaults:\n\n" + "\n".join(f"- `{p}`" for p in changed)
                       + "\n\nCheck the diff for changes this project made on purpose before merging.\n\n"
                         "Opened by `mason sync`."}) or {}
    say(f"   merge request: {mr.get('web_url', '')}")


def create(gl: GitLab, cfg, repo: str, group_name: str | None = None, public: bool = False,
           description: str = "") -> None:
    path = path_of(repo)
    ns, _, name = path.rpartition("/")
    if group_name:
        group = next((g for g in cfg.groups if g.name == group_name), None)
        if not group:
            raise RuntimeError(f"No group {group_name!r}")
    else:
        group = cfg.group_of(full(repo))
    say(f"== {full(repo)}" + (f" (group {group.name})" if group else " (no group: a plain project)"))
    try:
        gl.api("GET", gl.project(repo))
        raise RuntimeError(f"{full(repo)} exists already (mason doctor --fix applies the defaults)")
    except HttpError as err:
        if err.status != 404:
            raise
    namespace = gl.api("GET", f"/namespaces/{urllib.parse.quote(ns, safe='')}") or {}
    created = gl.write("POST", "/projects", {"name": name, "path": name, "namespace_id": namespace.get("id"),
                                             "visibility": "public" if public else "private",
                                             "description": description, "initialize_with_readme": True}) or {}
    if created:
        say(f"   created {created.get('web_url')}")
    if not group:
        return
    want = want_of(gl, cfg, group)
    if want["texts"]:
        gl.write("POST", f"{gl.project(repo)}/repository/commits", {
            "branch": created.get("default_branch") or "main", "commit_message": "chore: add the group's files",
            "actions": [{"action": "create", "file_path": p, "content": t} for p, t in want["texts"].items()]})
        say(f"   committed {len(want['texts'])} file(s)")
    if want["settings"]:
        gl.write("PUT", gl.project(repo), want["settings"])
    for b, levels in want["protected"].items():
        gl.write("DELETE", f"{gl.project(repo)}/protected_branches/{urllib.parse.quote(b, safe='')}")
        gl.write("POST", f"{gl.project(repo)}/protected_branches", {"name": b, **levels})
    say(f"   applied {len(want['settings'])} settings and {len(want['protected'])} protected branches")


# ── stats ────────────────────────────────────────────────────────────────────


def stats(gl: GitLab, repos: list[str], days: int = 30) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    from mason import stats as S

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    after = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    def one(repo: str) -> dict:
        p = gl.project(repo)
        try:
            merged = gl.api("GET", f"{p}/merge_requests?state=merged&updated_after={after}&per_page=100") or []
            opened = gl.api("GET", f"{p}/merge_requests?created_after={after}&per_page=100") or []
            open_now = gl.api("GET", f"{p}/merge_requests?state=opened&per_page=100") or []
            pipes = gl.api("GET", f"{p}/pipelines?updated_after={after}&per_page=100") or []
            rels = gl.api("GET", f"{p}/releases?per_page=30") or []
        except core.RateLimited:
            raise
        except RuntimeError:
            merged = opened = open_now = pipes = rels = []
        runs = []
        for x in pipes:
            dur = x.get("duration")
            runs.append((S.when(x.get("created_at")), x.get("status"), round(dur / 60, 1) if dur else None))
        return S.summarize(
            repo, "gitlab", [(S.when(m.get("created_at")), S.when(m.get("merged_at"))) for m in merged],
            [S.when(m.get("created_at")) for m in opened], [S.when(m.get("created_at")) for m in open_now], runs,
            [(r.get("tag_name"), S.when(r.get("released_at") or r.get("created_at"))) for r in rels], since, now)
    return core.pmap(one, repos)


# ── creating releases, and a project's releases in detail ────────────────────


def release_shape(r: dict) -> dict:
    """A GitLab release in the shape GitHub's REST API gives (what releases.py reads)."""
    links = (r.get("assets") or {}).get("links") or []
    return {"tag_name": r.get("tag_name"), "name": r.get("name") or "", "body": r.get("description") or "",
            "published_at": r.get("released_at") or r.get("created_at"), "created_at": r.get("created_at"),
            "html_url": (r.get("_links") or {}).get("self", ""), "draft": False,
            "prerelease": bool(r.get("upcoming_release")), "author": {"login": (r.get("author") or {}).get("username")},
            "target_commitish": (r.get("commit") or {}).get("short_id") or "",
            "assets": [{"name": a.get("name"), "url": a.get("url")} for a in links]}


def release_page(gl: GitLab, cfg, repo: str, tag: str | None = None) -> dict:
    """What a release page shows for a GitLab project (the same shape as releases.page)."""
    from mason import releases as REL
    rels = [release_shape(r) for r in gl.api("GET", f"{gl.project(repo)}/releases?per_page={REL.RECENT}") or []]
    try:
        pipes = gl.api("GET", f"{gl.project(repo)}/pipelines?per_page=30&scope=tags") or []
    except core.RateLimited:
        raise
    except RuntimeError:
        pipes = []
    runs = [{"name": f"pipeline #{p['id']}",
             "conclusion": "failure" if p.get("status") == "failed" else p.get("status"),
             "head_branch": p.get("ref"), "created_at": p.get("created_at"), "html_url": p.get("web_url")}
            for p in pipes]
    rel = REL.find(rels, tag)
    if tag and rel is None:
        raise RuntimeError(f"no release {tag} in {repo}")
    url = (rel or {}).get("html_url") or f"{gl.web}/{path_of(repo)}/-/releases"
    return {"repo": repo, "provider": "gitlab", "releases": [REL.row(r) for r in rels],
            "selected": (rel or {}).get("tag_name") or "", "url": url,
            "text": REL.detail_text(cfg, repo, rel, runs, rels), "suggest": REL.suggest_from(rels)}


def create_release(gl: GitLab, repo: str, tag: str, title: str = "", notes: str = "", draft: bool = False,
                   prerelease: bool = False, target: str = "", generate: bool = False) -> str:
    """Create a GitLab release (and its tag on `target` or the default branch when the tag is new).
    GitLab has no draft or pre-release flag, so those are refused rather than quietly published."""
    from mason import releases as REL
    tag = REL.valid_tag(tag)
    if draft or prerelease:
        raise RuntimeError("GitLab releases have no draft or pre-release state: leave those off for GitLab")
    title = (title or "").strip()[:200] or tag
    p = gl.project(repo)
    q = urllib.parse.quote(tag, safe="")
    try:
        gl.api("GET", f"{p}/releases/{q}")
        raise RuntimeError(f"{repo} has a release {tag} already")
    except HttpError as err:
        if err.status != 404:
            raise
    try:
        gl.api("GET", f"{p}/repository/tags/{q}")
        new_tag = False
    except HttpError as err:
        if err.status != 404:
            raise
        new_tag = True
    data = {"tag_name": tag, "name": title, "description": notes or ""}
    ref = (target or "").strip()
    if new_tag:
        ref = ref or (gl.api("GET", p) or {}).get("default_branch") or "main"
        data["ref"] = ref
    if generate and not notes:
        say("   GitLab doesn't write release notes itself: the release has none")
    where = f"a new tag on {ref}" if new_tag else "the existing tag"
    got = gl.write("POST", f"{p}/releases", data) or {}
    if core.dry_run():
        say(f"   [dry-run] create the release {tag} ({title}) of {repo} from {where}")
        return ""
    url = (got.get("_links") or {}).get("self") or f"{gl.web}/{path_of(repo)}/-/releases/{q}"
    say(f"   created the release {tag} of {repo} from {where}")
    say(f"      {url}")
    return url
