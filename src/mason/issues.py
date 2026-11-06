"""Issues across repos: `mason issues` lists and filters them, `mason issue` creates, shows, closes, reopens and
comments. GitHub here; Mason (the web app) adds GitLab issues (server/gitlab.py).
"""

from __future__ import annotations

import calendar
import time
import urllib.parse

from mason import core
from mason.core import WEB, ago, api, for_each, say


def _epoch(stamp: str) -> float:
    return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))


def row(repo: str, i: dict) -> dict:
    """One issue as the web app and `mason issues` show it."""
    return {"repo": repo, "provider": "github", "number": i["number"], "title": i.get("title") or "",
            "url": i.get("html_url") or f"{WEB}/{repo}/issues/{i['number']}", "state": i.get("state") or "",
            "labels": [lab["name"] if isinstance(lab, dict) else str(lab) for lab in i.get("labels") or []],
            "assignees": [a["login"] for a in i.get("assignees") or []],
            "author": (i.get("user") or {}).get("login", ""), "comments": i.get("comments") or 0,
            "created": i.get("created_at"), "updated": i.get("updated_at")}


def repo_issues(repo: str, state: str = "open", labels: list[str] | None = None, assignee: str | None = None,
                limit: int = 50) -> list[dict]:
    """Issues of one repo, newest update first; GitHub's list mixes in PRs, they're left out."""
    q = {"state": state, "sort": "updated", "direction": "desc", "per_page": str(min(max(limit, 1), 100))}
    if labels:
        q["labels"] = ",".join(labels)
    if assignee:
        q["assignee"] = assignee
    got = api("GET", f"/repos/{repo}/issues?{urllib.parse.urlencode(q)}") or []
    return [row(repo, i) for i in got if "pull_request" not in i][:limit]


def me() -> str:
    return (api("GET", "/user") or {}).get("login", "")


def stale(rows: list[dict], days: int) -> list[dict]:
    """Issues nobody touched for `days` days."""
    cutoff = time.time() - days * 86400
    return [r for r in rows if r.get("updated") and _epoch(r["updated"]) < cutoff]


# ── mason issues ───────────────────────────────────────────────────────────────


def report(repo: str, ctx: dict) -> None:
    rows = repo_issues(repo, ctx["state"], ctx["labels"], ctx["assignee"], ctx["limit"])
    if ctx.get("stale"):
        rows = stale(rows, ctx["stale"])
    if not rows:
        say("   no issues" if ctx["state"] == "open" else f"   no {ctx['state']} issues")
        return
    for r in rows:
        extra = []
        if r["labels"]:
            extra.append(f"[{', '.join(r['labels'])}]")
        if r["assignees"]:
            extra.append(" ".join(f"@{a}" for a in r["assignees"]))
        state = "" if ctx["state"] == "open" else f"{r['state']:<7}"
        when = ago(r["updated"]).replace(" ago", "")
        say(f"   #{r['number']:<5} {state}{r['title'][:70]}  {' '.join(extra)}  {when}".rstrip())


def issues(repos: list[str], state: str = "open", labels: list[str] | None = None, assignee: str | None = None,
           mine: bool = False, stale_days: int = 0, limit: int = 20) -> None:
    if mine:
        assignee = me()
    for_each(repos, report, {"state": state, "labels": labels or [], "assignee": assignee, "limit": limit,
                             "stale": stale_days})


# ── mason issue ────────────────────────────────────────────────────────────────


def create(repo: str, title: str, body: str = "", labels: list[str] | None = None,
           assignees: list[str] | None = None) -> str:
    """Open an issue; returns its URL."""
    if not title.strip():
        raise RuntimeError("an issue needs a title")
    data: dict = {"title": title.strip()[:256], "body": body}
    if labels:
        data["labels"] = labels
    if assignees:
        data["assignees"] = assignees
    got = api("POST", f"/repos/{repo}/issues", data) or {}
    url = got.get("html_url") or f"{WEB}/{repo}/issues"
    say(f"{'[dry-run] ' if core.dry_run() else ''}opened {url}")
    return url


def set_state(repo: str, number: int, close: bool, not_planned: bool = False) -> None:
    body = {"state": "closed" if close else "open"}
    if close:
        body["state_reason"] = "not_planned" if not_planned else "completed"
    api("PATCH", f"/repos/{repo}/issues/{number}", body)
    say(f"{'closed' if close else 'reopened'} {repo}#{number}")


def comment(repo: str, number: int, text: str) -> None:
    if not text.strip():
        raise RuntimeError("the comment is empty")
    api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": text})
    say(f"commented on {repo}#{number}")


def view(repo: str, number: int) -> None:
    i = api("GET", f"/repos/{repo}/issues/{number}") or {}
    r = row(repo, i)
    say(f"#{r['number']} {r['title']}\n{r['url']}\n")
    say(f"  state     {r['state']}{' (' + i['state_reason'] + ')' if i.get('state_reason') else ''}, "
        f"by {r['author']}, opened {ago(r['created'])}, updated {ago(r['updated'])}")
    if r["labels"]:
        say(f"  labels    {', '.join(r['labels'])}")
    if r["assignees"]:
        say(f"  assignees {', '.join(r['assignees'])}")
    if i.get("milestone"):
        say(f"  milestone {i['milestone'].get('title')}")
    if (i.get("body") or "").strip():
        say("\n" + i["body"].strip()[:4000])
    if r["comments"]:
        say(f"\nComments ({r['comments']}):")
        for c in api("GET", f"/repos/{repo}/issues/{number}/comments?per_page=20") or []:
            say(f"\n  {(c.get('user') or {}).get('login', '?')}, {ago(c.get('created_at'))}:")
            for line in (c.get("body") or "").strip().splitlines()[:20]:
                say(f"    {line}")
