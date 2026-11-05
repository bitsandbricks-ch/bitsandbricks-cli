"""`bnb stats`: how the repos are doing over the last N days.

Per repo: pull requests merged and opened, the median time to merge, open and stale PRs, workflow
runs with their success rate and median duration, and releases. Totals add a week-by-week series.
One GraphQL query per 20 repos and one REST call per repo; the operator shows the same numbers
(and GitLab's) as charts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import median

from bitsandbricks import core
from bitsandbricks.core import say, short, table

STALE_DAYS = 14
FIELDS = """
  merged: pullRequests(states: MERGED, last: 100, orderBy: {field: UPDATED_AT, direction: ASC}) {
    nodes { createdAt mergedAt } }
  open: pullRequests(states: OPEN, first: 100) { nodes { createdAt isDraft } }
  closed: pullRequests(states: CLOSED, last: 50, orderBy: {field: UPDATED_AT, direction: ASC}) {
    nodes { createdAt closedAt } }
  releases(first: 30, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { tagName createdAt } }
"""


def when(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def weeks(days: int, now: datetime) -> list[str]:
    out = []
    d = now - timedelta(days=days)
    while d <= now:
        if week(d) not in out:
            out.append(week(d))
        d += timedelta(days=1)
    return out


def summarize(repo: str, provider: str, merged: list[tuple], opened: list[datetime], open_now: list[datetime],
              runs: list[tuple], releases: list[tuple], since: datetime, now: datetime) -> dict:
    """merged: [(created, merged)]; runs: [(created, conclusion, minutes or None)]; releases: [(tag, created)]."""
    merged_in = [(c, m) for c, m in merged if m and m >= since]
    hours = [(m - c).total_seconds() / 3600 for c, m in merged_in if c]
    runs_in = [r for r in runs if r[0] and r[0] >= since]
    done = [r for r in runs_in if r[1] in ("success", "failure", "timed_out", "cancelled", "failed", "canceled")]
    ok = [r for r in done if r[1] == "success"]
    mins = [r[2] for r in runs_in if r[2] is not None]
    rel_in = [r for r in releases if r[1] and r[1] >= since]
    last_rel = max((r[1] for r in releases if r[1]), default=None)
    return {
        "repo": repo, "provider": provider,
        "merged": len(merged_in), "opened": sum(1 for d in opened if d >= since),
        "median_merge_hours": round(median(hours), 1) if hours else None,
        "open": len(open_now), "stale": sum(1 for d in open_now if (now - d).days >= STALE_DAYS),
        "oldest_open_days": max(((now - d).days for d in open_now), default=None),
        "runs": len(runs_in), "failed_runs": len(done) - len(ok),
        "success_rate": round(100 * len(ok) / len(done)) if done else None,
        "median_run_minutes": round(median(mins), 1) if mins else None,
        "releases": len(rel_in), "last_release_days": (now - last_rel).days if last_rel else None,
        "series": {"merged": [week(m) for _, m in merged_in], "runs": [week(r[0]) for r in runs_in],
                   "failed": [week(r[0]) for r in done if r[1] != "success"]},
    }


def github(repos: list[str], days: int = 30) -> list[dict]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    batches = [repos[i:i + 20] for i in range(0, len(repos), 20)]

    def nodes(batch: list[str]) -> dict:
        parts = []
        for i, repo in enumerate(batch):
            owner, name = repo.split("/", 1)
            parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ ...S }}")
        q = "query {\n" + "\n".join(parts) + "\n}\nfragment S on Repository {" + FIELDS + "}"
        try:
            data = core.graphql(q)
        except core.RateLimited:
            raise
        except RuntimeError:
            data = {}
        return {repo: data.get(f"r{i}") or {} for i, repo in enumerate(batch)}

    info: dict[str, dict] = {}
    for got in core.pmap(nodes, batches):
        info.update(got)

    def runs_of(repo: str) -> list[tuple]:
        try:
            found = (core.api("GET", f"/repos/{repo}/actions/runs?per_page=100&created=%3E%3D{since:%Y-%m-%d}")
                     or {}).get("workflow_runs") or []
        except core.RateLimited:
            raise
        except RuntimeError:
            return []
        out = []
        for r in found:
            start, end = when(r.get("run_started_at") or r.get("created_at")), when(r.get("updated_at"))
            mins = (end - start).total_seconds() / 60 if start and end and r.get("status") == "completed" else None
            out.append((when(r.get("created_at")), r.get("conclusion") or r.get("status"), mins))
        return out

    all_runs = dict(zip(repos, core.pmap(runs_of, repos)))
    out = []
    for repo in repos:
        n = info.get(repo) or {}
        merged = [(when(p.get("createdAt")), when(p.get("mergedAt")))
                  for p in (n.get("merged") or {}).get("nodes") or []]
        open_now = [when(p.get("createdAt")) for p in (n.get("open") or {}).get("nodes") or []]
        closed = [when(p.get("createdAt")) for p in (n.get("closed") or {}).get("nodes") or []]
        opened = [c for c, _ in merged if c] + open_now + [c for c in closed if c]
        rels = [(r.get("tagName"), when(r.get("createdAt"))) for r in (n.get("releases") or {}).get("nodes") or []]
        out.append(summarize(repo, "github", merged, opened, open_now, all_runs.get(repo, []), rels, since, now))
    return out


def totals(rows: list[dict], days: int) -> dict:
    now = datetime.now(timezone.utc)
    ws = weeks(days, now)

    def series(key: str) -> list[int]:
        return [sum(r["series"][key].count(w) for r in rows) for w in ws]

    merge_h = [r["median_merge_hours"] for r in rows if r["median_merge_hours"] is not None]
    rates = [(r["success_rate"], r["runs"]) for r in rows if r["success_rate"] is not None and r["runs"]]
    return {
        "repos": len(rows), "merged": sum(r["merged"] for r in rows), "opened": sum(r["opened"] for r in rows),
        "open": sum(r["open"] for r in rows), "stale": sum(r["stale"] for r in rows),
        "runs": sum(r["runs"] for r in rows), "failed_runs": sum(r["failed_runs"] for r in rows),
        "success_rate": round(sum(a * b for a, b in rates) / sum(b for _, b in rates)) if rates else None,
        "median_merge_hours": round(median(merge_h), 1) if merge_h else None,
        "releases": sum(r["releases"] for r in rows),
        "weeks": ws, "merged_by_week": series("merged"), "runs_by_week": series("runs"),
        "failed_by_week": series("failed"),
    }


def show(repos: list[str], days: int = 30) -> None:
    rows = github(repos, days)

    def cell(v, unit=""):
        return "-" if v is None else f"{v}{unit}"

    table(["repo", "merged", "opened", "merge time", "open", "stale", "runs", "ok", "run time", "releases"],
          [[short(r["repo"]), str(r["merged"]), str(r["opened"]), cell(r["median_merge_hours"], "h"), str(r["open"]),
            str(r["stale"]), str(r["runs"]), cell(r["success_rate"], "%"), cell(r["median_run_minutes"], "m"),
            str(r["releases"])] for r in rows if r["merged"] or r["open"] or r["runs"] or r["releases"]])
    t = totals(rows, days)
    say(f"\nlast {days} days, {t['repos']} repos: {t['merged']} PRs merged "
        f"(median {cell(t['median_merge_hours'], 'h')}),"
        f" {t['open']} open ({t['stale']} older than {STALE_DAYS} days), {t['runs']} runs"
        f" ({cell(t['success_rate'], '%')} green), {t['releases']} releases")
    bars = "▁▂▃▄▅▆▇█"
    top = max(t["merged_by_week"] or [0]) or 1
    say("merged per week  " + "".join(bars[min(7, int(7 * v / top))] for v in t["merged_by_week"]))
