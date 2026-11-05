"""Which repos bnb works on, how names resolve, and the everyday commands:
status, prs, pr, merge, runs, approve, security, open."""

from __future__ import annotations

import fnmatch
import json
import re
import webbrowser

from mortar import core, plugins
from mortar.config import Config
from mortar.core import WEB, ago, api, api_pages, for_each, graphql, newest, say, short, table

# ── the repo list ────────────────────────────────────────────────────────────

_me = core.Scoped("me")


def me() -> str:
    if "login" not in _me:
        _me.update(api("GET", "/user") or {})
    return _me.get("login", "")


def owner_repos(owner: str) -> list[dict]:
    """Every repo of an org or a user (private ones too where gh may see them)."""
    try:
        return api_pages(f"/orgs/{owner}/repos?type=all")
    except RuntimeError as err:
        if "404" not in str(err) and "Not Found" not in str(err):
            raise
    if owner.lower() == me().lower():
        return [r for r in api_pages("/user/repos?affiliation=owner") if r["owner"]["login"].lower() == owner.lower()]
    return api_pages(f"/users/{owner}/repos?type=owner")


_listing = core.Scoped("listing")  # {(orgs, repos, exclude): {repo: info}}


def listing(cfg: Config) -> dict[str, dict]:
    """{owner/name: {"topics": [...], "archived": bool}} of every repo bnb looks after, sorted."""
    key = (tuple(cfg.orgs), tuple(cfg.repos), tuple(cfg.exclude))
    if key in _listing:
        return _listing[key]
    found: dict[str, dict] = {}
    for items in core.pmap(owner_repos, cfg.orgs):
            for r in items:
                if not r.get("archived"):
                    found[r["full_name"]] = {"topics": r.get("topics") or [], "archived": False}
    for repo in cfg.repos:
        if repo not in found:
            try:
                r = api("GET", f"/repos/{repo}") or {}
                found[r.get("full_name") or repo] = {"topics": r.get("topics") or [], "archived": False}
            except RuntimeError:
                found[repo] = {"topics": [], "archived": False, "missing": True}
    found = {r: found[r] for r in sorted(found, key=str.lower) if not cfg.excluded(r)}
    _listing[key] = found
    return found


def group_of(cfg: Config, repo: str):
    return cfg.group_of(repo, (listing(cfg).get(repo) or {}).get("topics"))


def resolve(cfg: Config, arg: str) -> tuple[str, int | None]:
    """`owner/name`, `name`, a group alias (`budgets` for plugin-budgets), `x#12` or a PR URL
    -> (owner/name, PR number or None)."""
    m = re.match(r"(?:https?://github\.com/)?([\w.-]+/[\w.-]+)/pull/(\d+)", arg)
    if m:
        return m.group(1), int(m.group(2))
    name, _, num = arg.partition("#")
    if num and not num.isdigit():
        raise RuntimeError(f"Not a PR number: {arg}")
    number = int(num) if num else None
    if "/" in name:
        return name, number
    known = listing(cfg)
    hits = [r for r in known if short(r) == name]
    if not hits:
        hits = [r for r in known if (g := group_of(cfg, r)) and g.alias(r) == name]
    if len(hits) == 1:
        return hits[0], number
    if len(hits) > 1:
        raise RuntimeError(f"{name} is ambiguous: {', '.join(hits)}")
    if len(cfg.orgs) == 1:
        return f"{cfg.orgs[0]}/{name}", number
    raise RuntimeError(f"Unknown repo: {name}" + ("" if cfg.orgs else " (no orgs set: bnb init, or BNB_ORGS)"))


def pick_repos(cfg: Config, names: list[str], group: str | None = None) -> tuple[list[str], dict[str, list[int]]]:
    """The repos a command runs on (all of them when none are named) and any PR numbers named."""
    pick: dict[str, list[int]] = {}
    if not names:
        repos = [r for r in listing(cfg) if not listing(cfg)[r].get("missing")]
        if not repos:
            raise RuntimeError("No repos: name an org with bnb init <defaults repo>, BNB_ORGS or --org")
    else:
        repos = []
        for arg in names:
            repo, number = resolve(cfg, arg)
            if repo not in repos:
                repos.append(repo)
            if number:
                pick.setdefault(repo, []).append(number)
    if group:
        repos = [r for r in repos if (g := group_of(cfg, r)) and g.name == group]
        if not repos:
            groups = ", ".join(g.name for g in cfg.groups) or "none"
            raise RuntimeError(f"No repos in group {group!r}. Groups: {groups}")
    return repos, pick


# ── status and prs: one GraphQL query per 20 repos ───────────────────────────

REPO_FIELDS = """
  nameWithOwner isArchived
  defaultBranchRef { name target { ... on Commit { statusCheckRollup { state } } } }
  releases(first: 30, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { tagName } }
  pullRequests(states: OPEN, first: 30, orderBy: {field: UPDATED_AT, direction: DESC}) {
    totalCount
    nodes {
      number title url isDraft mergeable reviewDecision headRefName
      author { login }
      autoMergeRequest { enabledAt }
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    }
  }
  merged: pullRequests(states: MERGED, first: 5, orderBy: {field: UPDATED_AT, direction: DESC}) {
    nodes { number title url mergedAt mergedBy { login } }
  }
"""
BATCH = 20
_nodes = core.Scoped("nodes")


def nodes(repos: list[str], fresh: bool = False) -> dict[str, dict]:
    """The GraphQL node of each repo (REPO_FIELDS plus what plugins ask for)."""
    if fresh:
        _nodes.clear()
    todo = [r for r in repos if r not in _nodes]
    extra = "\n".join(p.graphql_fields for p in plugins.load() if p.graphql_fields)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    def one(batch: list[str]) -> dict:
        parts = []
        for i, repo in enumerate(batch):
            owner, name = repo.split("/", 1)
            parts.append(f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ ...R }}")
        query = "query {\n" + "\n".join(parts) + "\n}\nfragment R on Repository {" + REPO_FIELDS + extra + "}"
        try:
            data = graphql(query)
        except RuntimeError as err:
            if "Could not resolve" not in str(err):
                raise
            data = {}
            for i, repo in enumerate(batch):  # one repo is missing: ask one by one
                try:
                    data[f"r{i}"] = one([repo]).get(repo)
                except RuntimeError:
                    data[f"r{i}"] = None
            return {repo: data[f"r{i}"] for i, repo in enumerate(batch)}
        return {repo: data.get(f"r{i}") for i, repo in enumerate(batch)}

    for got in core.pmap(one, batches):
        _nodes.update(got)
    return {r: _nodes.get(r) for r in repos}


def checks_word(rollup: dict | None) -> str:
    state = (rollup or {}).get("state")
    return {"SUCCESS": "passing", "FAILURE": "failing", "ERROR": "failing",
            "PENDING": "running", "EXPECTED": "waiting"}.get(state, "no checks")


def status_columns() -> list:
    return plugins.hook("status_columns")


def status_head() -> list[str]:
    return ["repo", "group", "released", "PRs ready/draft", "CI", *(h for h, _ in status_columns())]


def status_rows(cfg: Config, repos: list[str]) -> list[list[str]]:
    info = nodes(repos)
    cols = status_columns()
    rows = []
    for repo in repos:
        node = info.get(repo)
        g = group_of(cfg, repo)
        if not node:
            rows.append([repo, g.name if g else "-", "missing", "", "", *("" for _ in cols)])
            continue
        last = newest([r["tagName"] for r in node["releases"]["nodes"]]) or "-"
        prs = node["pullRequests"]
        drafts = sum(p["isDraft"] for p in prs["nodes"])
        target = (node.get("defaultBranchRef") or {}).get("target") or {}
        ci = ((target.get("statusCheckRollup") or {}).get("state") or "-").lower()
        extra = []
        for _, fn in cols:
            try:
                extra.append(str(fn(repo, node)))
            except Exception:
                extra.append("?")
        rows.append([repo, g.name if g else "-", last, f"{prs['totalCount'] - drafts}/{drafts}", ci, *extra])
    return rows


def status(cfg: Config, repos: list[str]) -> None:
    table(status_head(), status_rows(cfg, repos))


def prs(cfg: Config, repos: list[str]) -> None:
    info = nodes(repos)
    total = 0
    for repo in repos:
        items = ((info.get(repo) or {}).get("pullRequests") or {}).get("nodes") or []
        if not items:
            continue
        say(f"== {repo}")
        for p in sorted(items, key=lambda p: p["number"]):
            total += 1
            commit = (p["commits"]["nodes"] or [{}])[0].get("commit") or {}
            bits = ["draft" if p["isDraft"] else "ready", f"checks {checks_word(commit.get('statusCheckRollup'))}"]
            if p.get("reviewDecision"):
                bits.append(p["reviewDecision"].lower().replace("_", " "))
            if p.get("mergeable") == "CONFLICTING":
                bits.append("CONFLICTS")
            if p.get("autoMergeRequest"):
                bits.append("auto-merge on")
            who = (p.get("author") or {}).get("login", "?")
            say(f"   #{p['number']} {p['title']}  ({who}, {p['headRefName']})")
            say(f"      {' · '.join(bits)}\n      {p['url']}")
    say(f"{total} open PR{'s' if total != 1 else ''}")


MERGE_STATE = {
    "CLEAN": "ready to merge",
    "BLOCKED": "blocked: required checks or reviews missing",
    "BEHIND": "behind the base branch: update it first",
    "DIRTY": "has merge conflicts",
    "UNSTABLE": "mergeable, but some checks fail",
    "HAS_HOOKS": "mergeable (with hooks)",
    "DRAFT": "draft",
    "UNKNOWN": "GitHub is still working it out",
}


def pr_detail(repo: str, number: int, diff: bool = False, full_body: bool = False) -> None:
    from mortar.transport import PR_DETAIL_FIELDS

    p = core.transport().pr_view(repo, number, PR_DETAIL_FIELDS)
    say(f"#{p['number']} {p['title']}\n{p['url']}\n")
    who = (p.get("author") or {}).get("login", "?")
    say(f"  state    {p['state'].lower()}{' (draft)' if p['isDraft'] else ''}, by {who}, "
        f"opened {ago(p.get('createdAt'))}, updated {ago(p.get('updatedAt'))}")
    say(f"  branch   {p['headRefName']} -> {p['baseRefName']}")
    say(f"  merge    {MERGE_STATE.get(p.get('mergeStateStatus'), p.get('mergeStateStatus') or '?')}"
        f"{'; auto-merge on' if p.get('autoMergeRequest') else ''}")
    say(f"  review   {(p.get('reviewDecision') or 'none required').lower().replace('_', ' ')}")
    if p.get("labels"):
        say(f"  labels   {', '.join(lab['name'] for lab in p['labels'])}")
    commits = p.get("commits") or []
    say(f"  size     {p['changedFiles']} files, +{p['additions']} -{p['deletions']}, "
        f"{len(commits)} commit{'' if len(commits) == 1 else 's'}")

    checks = p.get("statusCheckRollup") or []
    say(f"\nChecks ({len(checks)}):  {p['url']}/checks")
    for c in sorted(checks, key=lambda c: (c.get("conclusion") or c.get("state") or "") == "SUCCESS"):
        name = c.get("name") or c.get("context") or "?"
        if c.get("workflowName"):
            name = f"{c['workflowName']} / {name}"
        state = (c.get("conclusion") or c.get("state") or c.get("status") or "?").lower()
        say(f"  {state:<10} {name}")
        link = c.get("detailsUrl") or c.get("targetUrl")
        if link and state not in ("success", "skipped", "neutral"):
            say(f"             {link}")

    reviews = [r for r in p.get("reviews") or [] if r.get("state") != "COMMENTED" or r.get("body")]
    if reviews:
        say("\nReviews:")
        for r in reviews:
            say(f"  {r['state'].lower():<18} {(r.get('author') or {}).get('login', '?')}")
    say("\nCommits:")
    for c in commits[-15:]:
        say(f"  {c['oid'][:7]} {c['messageHeadline']}")
    say("\nFiles:")
    files = p.get("files") or []
    for f in sorted(files, key=lambda f: -(f["additions"] + f["deletions"]))[:40]:
        say(f"  +{f['additions']:<5} -{f['deletions']:<5} {f['path']}")
    if len(files) > 40:
        say(f"  ... and {len(files) - 40} more")
    body = (p.get("body") or "").strip()
    if body:
        lines = body.splitlines()
        cut = lines if full_body else lines[:25]
        say("\nDescription:\n  " + "\n  ".join(cut))
        if len(cut) < len(lines):
            say(f"  ... {len(lines) - len(cut)} more lines (--body for all)")
    if diff:
        say("\n" + core.transport().pr_diff(repo, number))


# ── merge ────────────────────────────────────────────────────────────────────

PR_FIELDS = "number,title,url,state,isDraft,mergeable,autoMergeRequest"


def merge_one(repo: str, p: dict, ctx: dict, named: bool) -> None:
    label = f"#{p['number']} {p['title']}"
    link = f"\n      {p['url']}" if p.get("url") else ""
    if p.get("state", "OPEN") != "OPEN":
        say(f"   {label}: {p['state'].lower()}, nothing to do{link}")
        return
    if p["isDraft"]:
        if not (named and ctx.get("ready")):
            hint = " (add --ready to merge it anyway)" if named else ""
            say(f"   {label}: draft, left open{hint}{link}")
            return
        if core.dry_run():
            say(f"   [dry-run] mark {label} ready for review")
        else:
            core.transport().pr_ready(repo, p["number"])
            say(f"   {label}: marked ready for review")
    if p.get("autoMergeRequest") and not ctx.get("now"):
        say(f"   {label}: auto-merge already on{link}\n      checks: {p['url']}/checks")
        return
    if p.get("mergeable") == "CONFLICTING":
        say(f"   {label}: has conflicts, left open{link}")
        return
    how = "merge now" if ctx.get("now") else "auto-merge"
    if core.dry_run():
        say(f"   [dry-run] {how} {label}{link}")
        return
    failed = core.transport().pr_merge(repo, p["number"], ctx.get("method") or "squash", auto=not ctx.get("now"))
    if failed:
        say(f"   {label}: not merged: {failed}{link}")
    elif ctx.get("now"):
        say(f"   {label}: merged{link}")
    else:
        say(f"   {label}: auto-merge on (merges when checks are green){link}\n      checks: {p['url']}/checks")


def merge(repo: str, ctx: dict) -> None:
    numbers = ctx.get("pick", {}).get(repo)
    if numbers:
        items = [core.transport().pr_view(repo, n, PR_FIELDS) for n in numbers]
    else:
        items = core.transport().pr_list_open(repo, PR_FIELDS)
    if not items:
        say("   no open PRs")
        return
    if any(p.get("state", "OPEN") == "OPEN" and not p["isDraft"] for p in items) and not ctx.get("now"):
        info = api("GET", f"/repos/{repo}") or {}
        if info.get("allow_auto_merge") is False:
            api("PATCH", f"/repos/{repo}", {"allow_auto_merge": True})
            say("   turned on auto-merge for the repo")
    for p in items:
        merge_one(repo, p, ctx, named=bool(numbers))


# ── runs and approvals ───────────────────────────────────────────────────────

# A run of an outside contributor's PR waits for "Approve and run" (status action_required).
RUN_APPROVAL = {"id": None, "name": "approval to run (outside contributor)"}


def waiting_runs(repo: str) -> list[tuple[str, dict, list[dict]]]:
    """Runs waiting for you: environments you review, and outside contributors' runs."""
    out = []
    found = api("GET", f"/repos/{repo}/actions/runs?status=waiting&per_page=20") or {}
    for run in found.get("workflow_runs") or []:
        pending = api("GET", f"/repos/{repo}/actions/runs/{run['id']}/pending_deployments") or []
        envs = [p["environment"] for p in pending if p.get("current_user_can_approve")]
        if envs:
            out.append((repo, run, envs))
        elif pending:  # waiting, but not for you: say why, so it isn't just missing
            out.append((repo, run, [dict(p["environment"], blocked=blocked_reason(repo, run, p)) for p in pending]))
    found = api("GET", f"/repos/{repo}/actions/runs?status=action_required&per_page=20") or {}
    for run in found.get("workflow_runs") or []:
        out.append((repo, run, [RUN_APPROVAL]))
    return out


def blocked_reason(repo: str, run: dict, pending: dict) -> str:
    """Why you can't approve a pending deployment."""
    env = (pending.get("environment") or {}).get("name", "?")
    names = [(r.get("reviewer") or {}).get("login") or (r.get("reviewer") or {}).get("slug") or "?"
             for r in pending.get("reviewers") or []]
    who = me()
    starter = (run.get("triggering_actor") or run.get("actor") or {}).get("login")
    if who in names and starter == who:
        return (f"you started this run and are the only way through, but \"Prevent self-review\" is on for {env}: "
                f"untick it ({WEB}/{repo}/settings/environments) or have another reviewer approve")
    return f"waits for {', '.join(names) or 'its reviewers'} ({env}); you aren't one of them"


def is_blocked(envs: list[dict]) -> bool:
    return all(e.get("blocked") for e in envs)


def approve_run(repo: str, run: dict, envs: list[dict]) -> None:
    envs = [e for e in envs if not e.get("blocked")]
    if RUN_APPROVAL in envs:
        api("POST", f"/repos/{repo}/actions/runs/{run['id']}/approve")
    ids = [e["id"] for e in envs if e is not RUN_APPROVAL and e.get("id") is not None]
    if ids:
        api("POST", f"/repos/{repo}/actions/runs/{run['id']}/pending_deployments",
            {"environment_ids": ids, "state": "approved", "comment": "Approved with bnb"})


def all_waiting(repos: list[str]) -> list[tuple[str, dict, list[dict]]]:
    def safe(repo):
        try:
            return waiting_runs(repo)
        except core.RateLimited:
            raise
        except RuntimeError:
            return []

    return [w for ws in core.pmap(safe, repos) for w in ws]


def approve(repos: list[str], yes: bool) -> None:
    found = all_waiting(repos)
    if not found:
        print("No runs are waiting for your approval.")
        return
    for repo, run, envs in found:
        names = ", ".join(e["name"] for e in envs)
        print(f"== {repo}  {run.get('head_branch')}  {run.get('name')} ({ago(run.get('created_at'))})")
        print(f"   waits for: {names}\n   {run['html_url']}")
        if is_blocked(envs):
            for e in envs:
                print(f"   you can't approve it: {e['blocked']}")
            continue
        if core.dry_run():
            print("   [dry-run] would approve")
            continue
        if not yes:
            try:
                answer = input(f"   Approve {names} for {repo} {run.get('head_branch')}? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                print("   left waiting")
                continue
        approve_run(repo, run, envs)
        print("   approved")


FAILED = ("failure", "timed_out", "cancelled", "startup_failure")


def run_jobs(repo: str, run_id: int) -> list[str]:
    """Every job of a run with its state, and the failed step of a failed one (one REST call)."""
    jobs = (api("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100") or {}).get("jobs") or []
    out = []
    for j in jobs:
        state = j.get("conclusion") or j.get("status") or "?"
        line = f"{state:<10} {j.get('name')}"
        if state in FAILED:
            steps = [st["name"] for st in j.get("steps") or [] if st.get("conclusion") == "failure"]
            if steps:
                line += f"  (step \"{steps[0]}\")"
            out += [line, f"           {j.get('html_url', '')}"]
        else:
            out.append(line)
    return out


def rerun_failed(repo: str, run: dict) -> None:
    """Re-run the failed jobs of a run (and what depends on them); approvals already given stay."""
    if core.dry_run():
        say(f"   [dry-run] re-run the failed jobs of {run.get('name')} on {run.get('head_branch')}")
        return
    api("POST", f"/repos/{repo}/actions/runs/{run['id']}/rerun-failed-jobs")
    say(f"   re-running the failed jobs of {run.get('name')} on {run.get('head_branch')}\n   {run.get('html_url', '')}")


ACTIVE = ("queued", "in_progress", "waiting", "requested", "pending")
LOG_NOISE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")  # the time stamp on each log line


def cancel_run(repo: str, run: dict) -> None:
    if core.dry_run():
        say(f"   [dry-run] cancel {run.get('name')} on {run.get('head_branch')}")
        return
    api("POST", f"/repos/{repo}/actions/runs/{run['id']}/cancel")
    say(f"   cancelling {run.get('name')} on {run.get('head_branch')}")


def failed_log(repo: str, run_id: int, lines: int = 40) -> list[str]:
    """The last lines of each failed job's log (time stamps dropped); where most failures explain themselves."""
    jobs = (api("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100") or {}).get("jobs") or []
    out = []
    for j in jobs:
        if (j.get("conclusion") or "") not in FAILED:
            continue
        try:
            text = core.download(f"/repos/{repo}/actions/jobs/{j['id']}/logs").decode(errors="replace")
        except RuntimeError as err:
            out += [f"── {j.get('name')}: no log ({err})", ""]
            continue
        tail = [LOG_NOISE.sub("", line) for line in text.splitlines() if line.strip()][-lines:]
        out += [f"── {j.get('name')} (last {len(tail)} lines)", *tail, ""]
    return out or ["no failed job with a log"]


def update_branch(repo: str, number: int) -> None:
    """Merge the base branch into the PR's branch (GitHub's Update branch button)."""
    if core.dry_run():
        say(f"   [dry-run] update the branch of {repo}#{number}")
        return
    api("PUT", f"/repos/{repo}/pulls/{number}/update-branch", {})
    say(f"   updating the branch of {repo}#{number} from its base")


def close_pr(repo: str, number: int) -> None:
    if core.dry_run():
        say(f"   [dry-run] close {repo}#{number}")
        return
    api("PATCH", f"/repos/{repo}/pulls/{number}", {"state": "closed"})
    say(f"   closed {repo}#{number}")


def comment_pr(repo: str, number: int, text: str) -> None:
    if core.dry_run():
        say(f"   [dry-run] comment on {repo}#{number}: {text[:60]}")
        return
    url = (api("POST", f"/repos/{repo}/issues/{number}/comments", {"body": text}) or {}).get("html_url", "")
    say(f"   commented on {repo}#{number}  {url}")


def rerun(repos: list[str], run_id: int | None, yes: bool) -> None:
    """bnb rerun: the failed jobs of each repo's newest failed run (or of --run ID)."""
    for repo in repos:
        if run_id:
            run = api("GET", f"/repos/{repo}/actions/runs/{run_id}") or {}
        else:
            found = (api("GET", f"/repos/{repo}/actions/runs?per_page=20") or {}).get("workflow_runs") or []
            run = next((r for r in found if r.get("conclusion") in FAILED), {})
        say(f"== {repo}")
        if not run or run.get("conclusion") not in FAILED:
            say("   no failed run" + (f" {run_id}" if run_id else " among the last 20"))
            continue
        say(f"   {run.get('conclusion')}  {run.get('name')} · {run.get('head_branch')} · {ago(run.get('created_at'))}")
        for line in run_jobs(repo, run["id"]):
            say(f"   {line}")
        if not yes and not core.dry_run():
            try:
                answer = input("   Re-run its failed jobs? [y/N] ")
            except EOFError:
                answer = ""
            if answer.strip().lower() not in ("y", "yes"):
                say("   left as it is")
                continue
        rerun_failed(repo, run)


def runs(repos: list[str], limit: int = 3, failed: bool = False) -> None:
    def fetch(repo: str):
        q = f"per_page={limit}" + ("&status=failure" if failed else "")
        try:
            return repo, (api("GET", f"/repos/{repo}/actions/runs?{q}") or {}).get("workflow_runs") or []
        except core.RateLimited:
            raise
        except RuntimeError as err:
            return repo, [{"error": str(err)}]

    results = core.pmap(fetch, repos)
    for repo, items in results:
        if not items:
            continue
        say(f"== {repo}")
        for r in items:
            if "error" in r:
                say(f"   error: {r['error']}")
                continue
            state = "waiting (approval)" if r.get("status") == "waiting" else (r.get("conclusion") or r.get("status"))
            say(f"   {state:<18} {r.get('name')} · {r.get('event')} · {r.get('head_branch')} · "
                f"{ago(r.get('created_at'))}\n      {r['html_url']}")


# ── releases ─────────────────────────────────────────────────────────────────

RELEASE_WORDS = ("release", "publish")


def is_release_run(run: dict) -> bool:
    """A run of a release workflow: triggered by a release, or named or filed like one."""
    text = f"{run.get('name') or ''} {run.get('path') or ''}".lower()
    return run.get("event") == "release" or any(w in text for w in RELEASE_WORDS)


def run_state(run: dict) -> str:
    return run.get("conclusion") or run.get("status") or "?"


def release_info(cfg: Config, repo: str) -> dict:
    """The newest release, the assets the group wants but the release lacks, and recent release runs.
    Two REST calls per repo."""
    info = {"repo": repo, "release": None, "missing": [], "runs": [], "error": ""}
    try:
        rels = api("GET", f"/repos/{repo}/releases?per_page=1") or []
        found = (api("GET", f"/repos/{repo}/actions/runs?per_page=30") or {}).get("workflow_runs") or []
    except core.RateLimited:
        raise
    except RuntimeError as err:
        info["error"] = str(err)
        return info
    rel = rels[0] if rels else None
    info["release"] = rel
    info["runs"] = [r for r in found if is_release_run(r)][:5]
    group = group_of(cfg, repo)
    if rel and group:
        names = [a.get("name", "") for a in rel.get("assets") or []]
        info["missing"] = [g for g in group.release_assets if not any(fnmatch.fnmatchcase(n, g) for n in names)]
    return info


def release_problems(info: dict) -> list[str]:
    """What's wrong: missing assets, a failed last release run, an error reading the repo."""
    out = []
    if info["error"]:
        out.append(f"error: {info['error']}")
    if info["missing"]:
        out.append("missing " + ", ".join(info["missing"]))
    last = info["runs"][0] if info["runs"] else None
    if last and last.get("conclusion") in ("failure", "timed_out", "startup_failure"):
        out.append(f"{last.get('name')} {run_state(last)}")
    return out


def release_row(info: dict) -> list[str]:
    rel = info["release"] or {}
    tag = rel.get("tag_name") or "-"
    if rel.get("draft"):
        tag += " (draft)"
    elif rel.get("prerelease"):
        tag += " (pre)"
    assets = rel.get("assets") or []
    if not rel:
        files = "-"
    elif info["missing"]:
        files = "missing " + ", ".join(info["missing"])
    else:
        files = ", ".join(a["name"] for a in assets[:2]) + (f" +{len(assets) - 2}" if len(assets) > 2 else "")
        files = files or "none"
    last = info["runs"][0] if info["runs"] else None
    run = f"{run_state(last)} {ago(last.get('created_at'))}" if last else "-"
    return [tag, ago(rel.get("published_at") or rel.get("created_at")) if rel else "-", files, run]


def release_infos(cfg: Config, repos: list[str]) -> list[dict]:
    """release_info for every repo that has a release or a release run."""
    found = core.pmap(lambda r: release_info(cfg, r), repos)
    return [i for i in found if i["release"] or i["runs"] or i["error"]]


def failed_jobs(repo: str, run_id: int) -> list[str]:
    """The failed jobs of a run with their failed steps and links (one REST call)."""
    jobs = (api("GET", f"/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest") or {}).get("jobs") or []
    out = []
    for j in jobs:
        if j.get("conclusion") not in ("failure", "timed_out", "cancelled"):
            continue
        steps = [s["name"] for s in j.get("steps") or [] if s.get("conclusion") == "failure"]
        out.append(f"{j.get('name')}: {j.get('conclusion')}" + (f" at step \"{steps[0]}\"" if steps else ""))
        out.append(f"  {j.get('html_url', '')}")
    return out


def release_text(info: dict) -> str:
    """The detail of one repo's releases: the release, its assets, recent release runs."""
    rel, lines = info["release"], [info["repo"], ""]
    if info["error"]:
        lines += [f"error: {info['error']}", ""]
    if rel:
        state = "draft" if rel.get("draft") else "prerelease" if rel.get("prerelease") else "published"
        lines += [f"{rel.get('tag_name')} · {state} · {ago(rel.get('published_at') or rel.get('created_at'))}",
                  rel.get("html_url", ""), ""]
        assets = rel.get("assets") or []
        lines.append(f"assets ({len(assets)})" if assets else "no assets")
        for a in assets:
            lines.append(f"  {a.get('name')}  {a.get('size', 0) / 1024:.0f} KB  {a.get('download_count', 0)} downloads")
        for g in info["missing"]:
            lines.append(f"  MISSING {g} (the group's release_assets)")
        notes = [line.rstrip() for line in (rel.get("body") or "").strip().splitlines()]
        if notes:
            lines += ["", "notes", *[f"  {line}" for line in notes[:25]]]
            if len(notes) > 25:
                lines.append(f"  … {len(notes) - 25} more lines: o opens the release")
    else:
        lines.append("no release")
    lines += ["", "release runs" if info["runs"] else "no release runs in the last 30 runs"]
    for r in info["runs"]:
        lines.append(f"  {run_state(r):<10} {r.get('name')} · {r.get('head_branch')} · {ago(r.get('created_at'))}")
        lines.append(f"    {r.get('html_url', '')}")
    return "\n".join(lines)


def releases(cfg: Config, repos: list[str], problems: bool = False) -> None:
    infos = release_infos(cfg, repos)
    if problems:
        infos = [i for i in infos if release_problems(i)]
    if not infos:
        say("no problems" if problems else "no releases")
        return
    table(["repo", "release", "when", "assets", "release run"], [[short(i["repo"]), *release_row(i)] for i in infos])
    for i in infos:
        bad = release_problems(i)
        if not bad:
            continue
        say(f"\n== {i['repo']}: " + "; ".join(bad))
        last = i["runs"][0] if i["runs"] else None
        if last and last.get("conclusion") == "failure":
            say(f"   {last.get('html_url', '')}")
            try:
                for line in failed_jobs(i["repo"], last["id"]):
                    say(f"   {line}")
            except core.RateLimited:
                raise
            except RuntimeError as err:
                say(f"   jobs: {err}")
        elif i["release"]:
            say(f"   {i['release'].get('html_url', '')}")


# ── security alerts ──────────────────────────────────────────────────────────

ALERT_KINDS = (("dependabot", "Dependabot"), ("secret-scanning", "secret scanning"), ("code-scanning", "code scanning"))
SEVERITY_ORDER = {"critical": 0, "high": 1, "secret": 1, "error": 2, "medium": 2, "moderate": 2,
                  "warning": 3, "low": 3, "note": 4}


def alert_row(kind: str, a: dict) -> dict:
    if kind == "dependabot":
        adv = a.get("security_advisory") or {}
        dep = a.get("dependency") or {}
        return {"severity": (adv.get("severity") or "?").lower(),
                "title": f"{(dep.get('package') or {}).get('name', '?')}: {adv.get('summary', '')}",
                "where": dep.get("manifest_path") or "", "url": a.get("html_url", "")}
    if kind == "secret-scanning":
        where = "push protection bypassed" if a.get("push_protection_bypassed") else ""
        return {"severity": "secret", "title": a.get("secret_type_display_name") or a.get("secret_type") or "secret",
                "where": where, "url": a.get("html_url", "")}
    rule = a.get("rule") or {}
    loc = (a.get("most_recent_instance") or {}).get("location") or {}
    return {"severity": (rule.get("security_severity_level") or rule.get("severity") or "?").lower(),
            "title": rule.get("description") or rule.get("id") or "?",
            "where": f"{loc.get('path', '')}:{loc.get('start_line', '')}" if loc.get("path") else "",
            "url": a.get("html_url", "")}


def repo_alerts(repo: str) -> tuple[list[dict], list[str]]:
    """Open alerts of all three kinds, most severe first, and what couldn't be read."""
    alerts, unread = [], []
    for kind, label in ALERT_KINDS:
        try:
            found = api("GET", f"/repos/{repo}/{kind}/alerts?state=open&per_page=100") or []
        except core.RateLimited:
            raise
        except RuntimeError as err:
            text = str(err)
            if kind == "code-scanning" and ("no analysis" in text.lower() or "404" in text):
                continue  # code scanning isn't set up: nothing to report
            hint = "off, or gh lacks access (gh auth refresh -s security_events)"
            unread.append(f"{label}: {hint if '403' in text or '404' in text else text[:120]}")
            continue
        alerts += [dict(alert_row(kind, a), kind=label) for a in found if isinstance(a, dict)]
    alerts.sort(key=lambda a: SEVERITY_ORDER.get(a["severity"], 5))
    return alerts, unread


def security_report(repo: str, ctx: dict) -> None:
    alerts, unread = repo_alerts(repo)
    if not alerts:
        say("   no open alerts" + (f" ({'; '.join(unread)})" if unread else ""))
    for a in alerts:
        say(f"   {a['severity']:<9} {a['kind']:<16} {a['title'][:80]}" + (f"  {a['where']}" if a["where"] else ""))
        say(f"      {a['url']}")
    if alerts and unread:
        say(f"   couldn't read: {'; '.join(unread)}")
    if alerts:
        say(f"   all: {WEB}/{repo}/security")


def security(repos: list[str]) -> None:
    for_each(repos, security_report, {})


# ── open ─────────────────────────────────────────────────────────────────────


def page_url(repo: str, number: int | None = None, actions: bool = False, releases: bool = False) -> str:
    url = f"{core.WEB}/{repo}"
    if number:
        url += f"/pull/{number}"
    elif actions:
        url += "/actions"
    elif releases:
        url += "/releases"
    return url


def open_page(repo: str, number: int | None, actions: bool, releases: bool) -> None:
    url = page_url(repo, number, actions, releases)
    say(url)
    if core.open_mode() == "browser":
        webbrowser.open(url)
