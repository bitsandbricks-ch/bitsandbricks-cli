"""Plumbing every command shares: output, the GitHub API, small helpers.

How bnb reaches GitHub is a *transport* (transport.py):

* the gh CLI (`gh auth login` once), so bnb never sees a token: the default;
* a token over HTTPS: what the operator (the web app) uses with each user's own OAuth token;
* an operator: `bnb login https://operator.example` makes every command and the TUI go
  through the operator with an API key, so the laptop needs neither gh nor a GitHub token.

The transport, dry run and command output are per context (contextvars), so the operator can
run commands for many users at once. Repos run side by side; each repo's lines are collected
and printed together, in the order the repos were named.
"""

from __future__ import annotations

import contextvars
import hashlib
import io
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone

WEB = "https://github.com"
WORKERS = 8  # repos handled at the same time
DRY_RUN = False  # the CLI's; a context can override it (set_dry_run)
CACHE: str | None = None  # e.g. "90s": GET answers come from a cache for that long (the TUI sets it)


class RateLimited(RuntimeError):
    """GitHub said: rate limit exceeded. `reset` is when it resets (epoch seconds), if known."""

    def __init__(self, message: str, reset: int | None = None):
        super().__init__(message)
        self.reset = reset


# ── per-context state ────────────────────────────────────────────────────────

_dry_run: contextvars.ContextVar[bool | None] = contextvars.ContextVar("bnb_dry_run", default=None)
_lines: contextvars.ContextVar[list | None] = contextvars.ContextVar("bnb_lines", default=None)
_printed: contextvars.ContextVar[io.StringIO | None] = contextvars.ContextVar("bnb_printed", default=None)


def dry_run() -> bool:
    value = _dry_run.get()
    return DRY_RUN if value is None else value


def set_dry_run(value: bool) -> None:
    """Dry run for this context (and the CLI's global, so code reading core.DRY_RUN agrees)."""
    global DRY_RUN
    _dry_run.set(bool(value))
    if not _context_stdout_installed():
        DRY_RUN = bool(value)


def transport():
    from mortar import transport as T
    return T.current()


@contextmanager
def using(t, dry: bool | None = None):
    """Run the block with transport `t` (and a dry run setting) in this context only."""
    from mortar import transport as T
    token = T.CURRENT.set(t)
    dtoken = _dry_run.set(dry) if dry is not None else None
    try:
        yield t
    finally:
        T.CURRENT.reset(token)
        if dtoken is not None:
            _dry_run.reset(dtoken)


def pmap(fn, items, workers: int = WORKERS) -> list:
    """pool.map that keeps this context (transport, dry run, output) in every worker."""
    items = list(items)
    if not items:
        return []
    ctx = contextvars.copy_context()
    with ThreadPoolExecutor(min(workers, len(items))) as pool:
        return list(pool.map(lambda x: ctx.copy().run(fn, x), items))


class Scoped:
    """A dict per transport: caches (who am I, the repo list, GraphQL nodes) never mix users."""

    def __init__(self, name: str):
        self.name = name

    def _d(self) -> dict:
        return transport().caches.setdefault(self.name, {})

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __contains__(self, k) -> bool:
        return k in self._d()

    def __len__(self) -> int:
        return len(self._d())

    def __iter__(self):
        return iter(self._d())

    def get(self, k, default=None):
        return self._d().get(k, default)

    def pop(self, k, default=None):
        return self._d().pop(k, default)

    def update(self, *a, **kw) -> None:
        self._d().update(*a, **kw)

    def clear(self) -> None:
        self._d().clear()

    def items(self):
        return self._d().items()


# ── output ───────────────────────────────────────────────────────────────────


def say(msg: str = "") -> None:
    lines = _lines.get()
    if lines is None:
        print(msg, flush=True)
    else:
        lines.append(msg)


def for_each(repos: list[str], action, ctx: dict, parallel: bool = True) -> None:
    """Run action(repo, ctx) for each repo under a `== repo` heading; errors stay with their repo."""
    def one(repo: str) -> list[str]:
        _lines.set([f"== {repo}"])
        try:
            action(repo, ctx)
        except RuntimeError as err:
            say(f"   error: {err}")
        return _lines.get() or []

    if not parallel:
        for repo in repos:
            say(f"== {repo}")
            try:
                action(repo, ctx)
            except RuntimeError as err:
                say(f"   error: {err}")
        return
    for lines in pmap(one, repos):
        say("\n".join(lines))


class _ContextStdout(io.TextIOBase):
    """sys.stdout for a server: what a context prints goes to that context's buffer (capture)."""

    def __init__(self, real):
        self.real = real

    def write(self, s: str) -> int:
        buf = _printed.get()
        if buf is not None:
            return buf.write(s)
        return self.real.write(s)

    def flush(self) -> None:
        if _printed.get() is None:
            self.real.flush()

    def isatty(self) -> bool:
        return False


def install_context_stdout() -> None:
    """Make print() context-aware (the operator calls this once): captures in parallel requests never mix."""
    if not isinstance(sys.stdout, _ContextStdout):
        sys.stdout = _ContextStdout(sys.stdout)
    if not isinstance(sys.stderr, _ContextStdout):
        sys.stderr = _ContextStdout(sys.stderr)


def _context_stdout_installed() -> bool:
    return isinstance(sys.stdout, _ContextStdout)


def capture(fn, *args, **kwargs) -> str:
    """What fn prints (via say or print), as text: the TUI and the operator show command output this way."""
    ltoken = _lines.set([])
    buf = io.StringIO()
    ptoken = _printed.set(buf)
    try:
        if _context_stdout_installed():
            install_context_stdout()  # someone may have swapped sys.stderr since (a test runner)
            fn(*args, **kwargs)
        else:
            with redirect_stdout(buf), redirect_stderr(buf):
                fn(*args, **kwargs)
    except RateLimited:
        raise
    except (RuntimeError, SystemExit) as err:
        if str(err) and str(err) != "None" and not str(err).isdigit():
            say(f"error: {err}")
    finally:
        lines = _lines.get() or []
        _lines.reset(ltoken)
        _printed.reset(ptoken)
    return "\n".join([*lines, buf.getvalue().rstrip()]).strip() + "\n"


# ── GitHub, through the transport ────────────────────────────────────────────


def gh(*args: str, stdin: str | None = None, check: bool = True) -> str:
    """Run gh (only with the gh transport); raises with gh's message on failure."""
    return transport().gh(*args, stdin=stdin, check=check)


def api(method: str, path: str, body: dict | None = None):
    """One GitHub REST call; returns the JSON answer. Writes only print in a dry run."""
    if dry_run() and method != "GET":
        say(f"   [dry-run] {method} {path}")
        return {}
    return transport().api(method, path, body)


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
    """Bytes of a release asset (REST /releases/assets/<id>); private repos work too."""
    return transport().download(path)


def graphql(query: str, **variables) -> dict:
    """A read-only GraphQL query (runs in dry runs too)."""
    return transport().graphql(query, variables)


def rate_limit() -> dict:
    """{"core": {"remaining", "limit", "reset"}, "graphql": {...}}; asking costs nothing. {} when unknown."""
    try:
        return transport().rate_limit()
    except Exception:
        return {}


def exists(repo: str) -> bool:
    return transport().exists(repo)


def get_file(repo: str, path: str, ref: str | None = None) -> tuple[str, str] | None:
    """(text, blob sha) of a file, or None when it doesn't exist."""
    import base64

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
    import base64

    head = api("GET", f"/repos/{repo}/git/ref/heads/{base}")["object"]["sha"]
    api("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": head})
    for path, (text, sha) in files.items():
        body_ = {"message": message, "branch": branch, "content": base64.b64encode(text.encode()).decode()}
        if sha:
            body_["sha"] = sha
        api("PUT", f"/repos/{repo}/contents/{path}", body_)
    if dry_run():
        say(f"   [dry-run] open a PR {branch} -> {base}: {title}")
        return f"{WEB}/{repo}/pulls"
    return transport().pr_create(repo, base, branch, title, body)


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
