"""Workflow hardening: `mtr workflows` (and doctor, for groups with `workflows = ...`).

Reads each repo's .github/workflows/*.yml and flags what lets an attacker in:

* actions on a tag or branch instead of a commit SHA (a moved tag runs someone else's code),
* `permissions: write-all`, or no top-level `permissions` (jobs get the repo's default token),
* `pull_request_target` workflows that check out the PR's code (it runs with secrets),
* `${{ github.event... }}` text from issues, PRs and commits straight in a `run:` script (script injection).

`mtr workflows --fix` opens a PR per repo that pins every action to the commit its tag points at now,
keeping the tag as a comment (`uses: actions/checkout@<sha> # v4`). The rest needs a person.

The workflows are read line by line, not as YAML: no dependency, and line numbers stay exact.
"""

from __future__ import annotations

import fnmatch
import re
import secrets

from mortar import core
from mortar.core import api, file_on, for_each, say

KINDS = ("pinned", "permissions", "pull_request_target", "injection")
SHA = re.compile(r"^[0-9a-f]{40}$")
USES = re.compile(r"""^(?P<lead>\s*(?:-\s+)?uses:\s*)(?P<q>['"]?)(?P<value>[^'"\s#]+)(?P=q)(?P<rest>\s*(?:#.*)?)$""")
RUN = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?run:\s*(?P<rest>.*)$")
PERMISSIONS = re.compile(r"^(?P<indent>\s*)permissions:\s*(?P<value>[^#]*?)\s*(?:#.*)?$")
UNTRUSTED = re.compile(
    r"\$\{\{\s*(github\.head_ref|github\.event\.[\w.*\[\]'-]*?"
    r"\.(?:title|body|message|name|email|label|ref|page_name|default_branch))\s*\}\}")
HEAD_CHECKOUT = re.compile(r"\bref:\s*['\"]?\$\{\{\s*(github\.event\.pull_request\.head\.|github\.head_ref)")


def settings(raw) -> dict:
    """A group's `workflows`: true (every check), false, or a table of checks plus `allow_unpinned`."""
    if raw is True:
        return {k: True for k in KINDS} | {"allow_unpinned": []}
    if not raw:
        return {}
    out = {k: bool(raw.get(k, True)) for k in KINDS}
    out["allow_unpinned"] = core_list(raw.get("allow_unpinned"))
    return out


def core_list(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


def allowed(action: str, allow: list[str]) -> bool:
    """`allow_unpinned = ["actions/*", "your-org/*"]`: owners or actions that may stay on a tag."""
    name = "/".join(action.split("/")[:2])
    return any(fnmatch.fnmatchcase(name, p) for p in allow)


# ── reading a workflow ───────────────────────────────────────────────────────


def scan(path: str, text: str, want: dict | None = None) -> list[dict]:
    """Findings in one workflow file: {path, line, kind, text, action?, ref?}."""
    want = want or settings(True)
    lines = text.splitlines()
    found: list[dict] = []

    def add(n: int, kind: str, msg: str, **extra) -> None:
        found.append({"path": path, "line": n, "kind": kind, "text": msg, **extra})

    run_lines: set[int] = set()
    i = 0
    while i < len(lines):
        m = RUN.match(lines[i])
        if m:
            run_lines.add(i)
            if m.group("rest").strip()[:1] in ("|", ">"):
                indent = len(m.group("indent")) + len(m.group("dash") or "")
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                    run_lines.add(j)
                    j += 1
                i = j
                continue
        i += 1

    top_permissions = False
    target = None
    checkout = None
    for n, line in enumerate(lines, 1):
        if n - 1 in run_lines:
            if want.get("injection"):
                for expr in UNTRUSTED.findall(line):
                    add(n, "injection", f"{expr} goes straight into a shell: pass it in env: and use \"$VAR\"")
            continue
        if line.lstrip().startswith("#"):
            continue
        m = USES.match(line)
        if m and want.get("pinned"):
            value = m.group("value")
            if not value.startswith(("./", "docker://")) and "@" in value:
                action, ref = value.rsplit("@", 1)
                if not SHA.match(ref) and not allowed(action, want.get("allow_unpinned") or []):
                    add(n, "pinned", f"{action}@{ref} isn't pinned to a commit", action=action, ref=ref)
            elif not value.startswith(("./", "docker://")):
                add(n, "pinned", f"{value} names no version at all", action=value, ref="")
        m = PERMISSIONS.match(line)
        if m:
            if not m.group("indent"):
                top_permissions = True
            if want.get("permissions") and m.group("value").strip("'\"") == "write-all":
                add(n, "permissions", "permissions: write-all gives every job a token that can change everything")
        if re.search(r"\bpull_request_target\b", line) and target is None:
            target = n
        if HEAD_CHECKOUT.search(line) and checkout is None:
            checkout = n
    if want.get("permissions") and not top_permissions and lines:
        add(1, "permissions", "no top-level permissions: jobs get the repo's default token "
                              "(add `permissions: contents: read`)")
    if want.get("pull_request_target") and target and checkout:
        add(checkout, "pull_request_target", f"pull_request_target (line {target}) checks out the PR's code, "
                                             "which then runs with the repo's secrets")
    return sorted(found, key=lambda f: f["line"])


def workflow_files(repo: str, ref: str | None = None) -> dict[str, tuple[str, str]]:
    """{path: (text, blob sha)} of .github/workflows/*.yml and *.yaml."""
    q = f"?ref={ref}" if ref else ""
    try:
        listing = api("GET", f"/repos/{repo}/contents/.github/workflows{q}") or []
    except RuntimeError:
        return {}
    out = {}
    for f in sorted((x for x in listing if isinstance(x, dict)), key=lambda x: x.get("name", "")):
        if f.get("type", "file") == "file" and f.get("name", "").endswith((".yml", ".yaml")):
            path = f".github/workflows/{f['name']}"
            text = file_on(repo, path, ref)
            if text is not None:
                out[path] = (text, f.get("sha") or core.git_blob_sha(text))
    return out


def findings(repo: str, want: dict | None = None, files: dict | None = None) -> list[dict]:
    files = workflow_files(repo) if files is None else files
    return [f for path, (text, _) in files.items() for f in scan(path, text, want)]


# ── pinning ──────────────────────────────────────────────────────────────────

_resolved = core.Scoped("workflows.resolved")


def resolve(action: str, ref: str) -> str | None:
    """The commit SHA `ref` of an action's repo points at now (None when it can't be read)."""
    owner_repo = "/".join(action.split("/")[:2])
    key = (owner_repo, ref)
    if key not in _resolved:
        try:
            _resolved[key] = (api("GET", f"/repos/{owner_repo}/commits/{ref}") or {}).get("sha")
        except RuntimeError:
            _resolved[key] = None
    return _resolved[key]


def pin(text: str, want: dict | None = None) -> tuple[str, list[str], list[str]]:
    """The workflow with its actions pinned: (new text, what changed, what couldn't be resolved)."""
    want = want or settings(True)
    out, changed, failed = [], [], []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = USES.match(body)
        if m:
            value = m.group("value")
            if "@" in value and not value.startswith(("./", "docker://")):
                action, ref = value.rsplit("@", 1)
                if not SHA.match(ref) and not allowed(action, want.get("allow_unpinned") or []):
                    sha = resolve(action, ref)
                    if sha:
                        q = m.group("q")
                        line = f"{m.group('lead')}{q}{action}@{sha}{q} # {ref}" + line[len(body):]
                        changed.append(f"{action}@{ref}")
                    else:
                        failed.append(f"{action}@{ref}")
        out.append(line)
    return "".join(out), changed, failed


# ── the command ──────────────────────────────────────────────────────────────


def want_of(cfg, repo: str) -> dict:
    """The repo's group's `workflows` settings, or every check when its group sets none."""
    from mortar.repos import group_of

    group = group_of(cfg, repo) if cfg else None
    return settings(group.workflows if group and group.workflows else True)


def report(repo: str, ctx: dict) -> None:
    want = want_of(ctx.get("cfg"), repo)
    files = workflow_files(repo)
    if not files:
        say("   no workflows")
        return
    found = findings(repo, want, files)
    if ctx.get("fix"):
        _url, failed = fix_repo(repo, want, files)
        found = [f for f in found if f["kind"] != "pinned" or f"{f['action']}@{f['ref']}" in failed]
    if not found:
        say(f"   ok ({len(files)} workflows)")
        return
    for f in found:
        say(f"   - {f['path'].rsplit('/', 1)[-1]}:{f['line']} {f['text']}")
    if any(f["kind"] == "pinned" for f in found):
        say(f"   fix: mtr workflows --fix {repo}  (PR that pins the actions)")


def fix_repo(repo: str, want: dict, files: dict) -> tuple[str | None, set[str]]:
    """Open a PR that pins the actions of every workflow: (its URL, the actions left unpinned)."""
    changes, all_changed, all_failed = {}, [], []
    for path, (text, sha) in files.items():
        new, changed, failed = pin(text, want)
        all_failed += failed
        if changed:
            changes[path] = (new, sha)
            all_changed += changed
    for f in sorted(set(all_failed)):
        say(f"   couldn't resolve {f}: left as it is")
    if not changes:
        if not all_failed:
            say("   every action is pinned")
        return None, set(all_failed)
    base = (api("GET", f"/repos/{repo}") or {}).get("default_branch") or "main"
    names = sorted(set(all_changed))
    body = ("Pins every action to the commit its tag points at today, and keeps the tag as a comment so "
            "Dependabot or Renovate can keep updating it.\n\n" + "\n".join(f"- `{n}`" for n in names) +
            "\n\nOpened by Mortar (`mtr workflows --fix`).")
    url = core.open_pr(repo, base, f"mortar/pin-actions-{secrets.token_hex(3)}", changes,
                       "Pin actions to commit SHAs", "Pin GitHub Actions to commit SHAs", body)
    say(f"   PR: {url}  (pins {len(names)} {'action' if len(names) == 1 else 'actions'} "
        f"in {len(changes)} {'workflow' if len(changes) == 1 else 'workflows'})")
    return url, set(all_failed)


def workflows(cfg, repos: list[str], fix: bool = False) -> None:
    for_each(repos, report, {"cfg": cfg, "fix": fix})
