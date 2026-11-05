"""Plumbing every command shares: output, gh, the GitHub API, small helpers.

Everything goes through the gh CLI (`gh auth login` once), so bnb never sees a
token. Repos run side by side; each repo's lines are collected and printed
together, in the order the repos were named.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timezone

WEB = "https://github.com"
WORKERS = 8  # repos handled at the same time
DRY_RUN = False

_out = threading.local()


def say(msg: str = "") -> None:
    lines = getattr(_out, "lines", None)
    if lines is None:
        print(msg, flush=True)
    else:
        lines.append(msg)


def for_each(repos: list[str], action, ctx: dict, parallel: bool = True) -> None:
    """Run action(repo, ctx) for each repo under a `== repo` heading; errors stay with their repo."""
    def one(repo: str) -> list[str]:
        _out.lines = [f"== {repo}"]
        try:
            action(repo, ctx)
        except RuntimeError as err:
            say(f"   error: {err}")
        lines, _out.lines = _out.lines, None
        return lines

    if not parallel:
        for repo in repos:
            say(f"== {repo}")
            try:
                action(repo, ctx)
            except RuntimeError as err:
                say(f"   error: {err}")
        return
    with ThreadPoolExecutor(WORKERS) as pool:
        for lines in pool.map(one, repos):
            say("\n".join(lines))


def capture(fn, *args, **kwargs) -> str:
    """What fn prints (via say or print), as text: the TUI shows command output this way."""
    _out.lines = []
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            fn(*args, **kwargs)
    except (RuntimeError, SystemExit) as err:
        say(f"error: {err}")
    lines, _out.lines = _out.lines, None
    return "\n".join([*lines, buf.getvalue().rstrip()]).strip() + "\n"


# ── gh ───────────────────────────────────────────────────────────────────────


def gh(*args: str, stdin: str | None = None, check: bool = True) -> str:
    """Run gh and return its output; raises with gh's message on failure."""
    proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def api(method: str, path: str, body: dict | None = None):
    """One GitHub API call through `gh api`; returns the JSON answer. Writes only print in a dry run."""
    if DRY_RUN and method != "GET":
        say(f"   [dry-run] {method} {path}")
        return {}
    args = ["api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    if body is not None:
        args += ["--input", "-"]
    out = gh(*args, stdin=json.dumps(body) if body is not None else None)
    return json.loads(out) if out.strip() else None


def api_pages(path: str, key: str | None = None) -> list:
    """Every page of a GET list endpoint (100 a page). `key` picks the list out of an object answer."""
    found, page = [], 1
    sep = "&" if "?" in path else "?"
    while True:
        data = api("GET", f"{path}{sep}per_page=100&page={page}") or []
        items = (data.get(key) or []) if key and isinstance(data, dict) else data
        found += items
        if len(items) < 100:
            return found
        page += 1


def download(path: str) -> bytes:
    """Bytes of a release asset (REST /releases/assets/<id>), through gh so private repos work too."""
    proc = subprocess.run(["gh", "api", path, "-H", "Accept: application/octet-stream"],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path}: {proc.stderr.decode(errors='replace').strip()[:300]}")
    return proc.stdout


def graphql(query: str, **variables) -> dict:
    """A read-only GraphQL query (runs in dry runs too)."""
    out = gh("api", "graphql", "--input", "-", stdin=json.dumps({"query": query, "variables": variables}))
    data = json.loads(out)
    if data.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in data["errors"])[:300])
    return data["data"]


def exists(repo: str) -> bool:
    return subprocess.run(["gh", "repo", "view", repo], capture_output=True, check=False).returncode == 0


def get_file(repo: str, path: str, ref: str | None = None) -> tuple[str, str] | None:
    """(text, blob sha) of a file, or None when it doesn't exist."""
    q = f"?ref={ref}" if ref else ""
    try:
        data = api("GET", f"/repos/{repo}/contents/{path}{q}")
    except RuntimeError as err:
        if "404" in str(err) or "Not Found" in str(err):
            return None
        raise
    return base64.b64decode(data["content"]).decode(), data["sha"]


def file_on(repo: str, path: str, ref: str | None = None) -> str | None:
    try:
        got = get_file(repo, path, ref)
    except (RuntimeError, KeyError, TypeError):
        return None
    return got[0] if got else None


def open_pr(repo: str, base: str, branch: str, files: dict[str, tuple[str, str | None]],
            message: str, title: str, body: str) -> str:
    """Commit `files` ({path: (new text, old blob sha or None)}) on a new branch and open a PR."""
    head = api("GET", f"/repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
    api("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": head})
    for path, (text, sha) in files.items():
        body_ = {"message": message, "branch": branch, "content": base64.b64encode(text.encode()).decode()}
        if sha:
            body_["sha"] = sha
        api("PUT", f"/repos/{repo}/contents/{path}", body_)
    return gh("pr", "create", "--repo", repo, "--base", base, "--head", branch,
              "--title", title, "--body", body).strip()


# ── small helpers ────────────────────────────────────────────────────────────


def short(repo: str) -> str:
    return repo.split("/", 1)[-1]


def git_blob_sha(text: str) -> str:
    """The sha git gives a file with this content: compares a file with GitHub's tree without downloading it."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def version_tuple(v: str) -> tuple:
    parts = [int(p) for p in re.findall(r"\d+", v.lstrip("v").split("-")[0])[:3]]
    return tuple(parts + [0] * (3 - len(parts)))


def newest(tags: list[str]) -> str | None:
    return max(tags, key=version_tuple, default=None)


def ago(stamp: str | None) -> str:
    if not stamp:
        return "-"
    secs = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{int(secs // size)}{unit} ago"
    return "just now"


def table(head: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    for r in [head, ["-" * w for w in widths], *rows]:
        say("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


def compact(value) -> str:
    if value is None:
        return "unset"
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
    return text if len(text) <= 60 else text[:57] + "..."


def open_mode() -> str:
    """How links open: BNB_OPEN=browser|copy|print. Over SSH the default is copy: a browser would
    start on the remote machine (or a text browser inside the terminal)."""
    mode = (os.environ.get("BNB_OPEN") or os.environ.get("HUBRA_OPEN") or "").strip().lower()
    if mode in ("browser", "copy", "print"):
        return mode
    return "copy" if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") else "browser"
