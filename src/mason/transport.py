"""How mason talks to GitHub.

GhTransport        the gh CLI (the default): mason never sees a token.
HttpTransport      a token over HTTPS (stdlib only): the operator uses one per user and request.
OperatorTransport  an operator's /api/v1/github proxy with an API key (`mason login <url>`):
                   the CLI and the TUI work without gh or a GitHub token on this machine.

All three answer the same calls; core picks the one of the current context.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from mason.cache import Spaces, Store, graphql_ttl, ttl_for

CURRENT: contextvars.ContextVar = contextvars.ContextVar("mason_transport", default=None)
_default = None
_lock = threading.Lock()


def current():
    t = CURRENT.get()
    if t is not None:
        return t
    global _default
    with _lock:
        if _default is None:
            _default = from_environment()
        return _default


def set_default(t) -> None:
    global _default
    _default = t


def from_environment():
    """The operator when `mason login` saved one (or MASON_SERVER is set), else gh."""
    url, key = operator_login()
    if url and key:
        return OperatorTransport(url, key)
    return GhTransport()


# ── the operator login of the CLI ────────────────────────────────────────────


def config_dir() -> Path:
    """~/.config/mason (or $XDG_CONFIG_HOME/mason)."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")) / "mason"


def key_file() -> Path:
    return config_dir() / "operator.json"


def operator_login() -> tuple[str, str]:
    """(operator URL, API key): MASON_SERVER and MASON_KEY, else what `mason login` saved."""
    url = os.environ.get("MASON_SERVER", "").strip()
    key = os.environ.get("MASON_KEY", "").strip()
    if url and key:
        return url.rstrip("/"), key
    if os.environ.get("MASON_SERVER") == "":  # set but empty: gh, even with a saved login
        return "", ""
    try:
        saved = json.loads(key_file().read_text())
    except (OSError, ValueError):
        return "", ""
    return (url or saved.get("url", "")).rstrip("/"), saved.get("key", "")


def save_operator_login(url: str, key: str) -> Path:
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": url.rstrip("/"), "key": key}, f)
    os.chmod(path, 0o600)
    return path


def forget_operator_login() -> bool:
    try:
        key_file().unlink()
        return True
    except FileNotFoundError:
        return False


def ttl_seconds(cache: str | None) -> int:
    if not cache:
        return 0
    cache = cache.strip().lower()
    unit = {"s": 1, "m": 60, "h": 3600}.get(cache[-1:], 1)
    digits = cache.rstrip("smh")
    return int(digits) * unit if digits.isdigit() else 0


def _rate_error(reset: int | None, label: str = "GitHub"):
    from mason.core import RateLimited
    when = datetime.fromtimestamp(reset).strftime("%H:%M") if reset else "soon"
    return RateLimited(f"{label} rate limit reached, it resets at {when}", reset)


# ── gh ───────────────────────────────────────────────────────────────────────

PR_DETAIL_FIELDS = ("number,title,url,state,isDraft,author,baseRefName,headRefName,createdAt,"
                    "updatedAt,mergeable,mergeStateStatus,reviewDecision,autoMergeRequest,"
                    "additions,deletions,changedFiles,files,commits,statusCheckRollup,reviews,"
                    "labels,body")


class GhTransport:
    """The gh CLI; an optional token (GH_TOKEN) lets one process act for someone else."""

    name = "gh"
    web = "https://github.com"

    def __init__(self, token: str | None = None):
        self.token = token
        self.caches = Spaces()

    def _env(self):
        if not self.token:
            return None
        return {**os.environ, "GH_TOKEN": self.token}

    def gh(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        proc = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False,
                              env=self._env())
        if proc.returncode != 0 and "rate limit" in proc.stderr.lower():
            raise _rate_error((self.rate_limit().get("core") or {}).get("reset"))
        if check and proc.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:4])}: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def _cache(self) -> list[str]:
        from mason import core
        return ["--cache", core.CACHE] if core.CACHE else []

    def api(self, method: str, path: str, body: dict | None = None):
        args = ["api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
        if method == "GET":
            args += self._cache()
        if body is not None:
            args += ["--input", "-"]
        out = self.gh(*args, stdin=json.dumps(body) if body is not None else None)
        return json.loads(out) if out.strip() else None

    def graphql(self, query: str, variables: dict) -> dict:
        out = self.gh("api", "graphql", *self._cache(), "--input", "-",
                      stdin=json.dumps({"query": query, "variables": variables}))
        data = json.loads(out)
        if data.get("errors"):
            raise RuntimeError("; ".join(e.get("message", "?") for e in data["errors"])[:300])
        return data["data"]

    def download(self, path: str) -> bytes:
        proc = subprocess.run(["gh", "api", path, "-H", "Accept: application/octet-stream"],
                              capture_output=True, check=False, env=self._env())
        if proc.returncode != 0:
            raise RuntimeError(f"gh api {path}: {proc.stderr.decode(errors='replace').strip()[:300]}")
        return proc.stdout

    def rate_limit(self) -> dict:
        proc = subprocess.run(["gh", "api", "/rate_limit"], capture_output=True, text=True, check=False,
                              env=self._env())
        try:
            return json.loads(proc.stdout).get("resources") or {} if proc.returncode == 0 else {}
        except ValueError:
            return {}

    def logged_in(self) -> bool:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, check=False,
                              env=self._env()).returncode == 0

    def exists(self, repo: str) -> bool:
        return subprocess.run(["gh", "repo", "view", repo], capture_output=True, check=False,
                              env=self._env()).returncode == 0

    def pr_view(self, repo: str, number: int, fields: str) -> dict:
        return json.loads(self.gh("pr", "view", str(number), "--repo", repo, "--json", fields))

    def pr_list_open(self, repo: str, fields: str) -> list[dict]:
        return json.loads(self.gh("pr", "list", "--repo", repo, "--state", "open", "--json", fields))

    def pr_diff(self, repo: str, number: int) -> str:
        return self.gh("pr", "diff", str(number), "--repo", repo)

    def pr_ready(self, repo: str, number: int) -> None:
        self.gh("pr", "ready", str(number), "--repo", repo)

    def pr_merge(self, repo: str, number: int, method: str = "squash", auto: bool = True) -> str:
        """'' when it worked, else why not."""
        cmd = ["gh", "pr", "merge", str(number), "--repo", repo, f"--{method}", "--delete-branch"]
        if auto:
            cmd.append("--auto")
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=self._env())
        return "" if proc.returncode == 0 else (proc.stderr.strip()[:200] or "failed")

    def pr_create(self, repo: str, base: str, head: str, title: str, body: str) -> str:
        return self.gh("pr", "create", "--repo", repo, "--base", base, "--head", head,
                       "--title", title, "--body", body).strip()

    def repo_create(self, repo: str, public: bool, template: str | None, description: str) -> None:
        args = ["repo", "create", repo, "--public" if public else "--private"]
        args += ["--template", template] if template else ["--add-readme"]
        if description:
            args += ["--description", description]
        self.gh(*args)


# ── HTTPS with a token ───────────────────────────────────────────────────────

PR_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      id number title url state isDraft baseRefName headRefName createdAt updatedAt
      mergeable mergeStateStatus reviewDecision additions deletions changedFiles body
      author { login }
      autoMergeRequest { enabledAt }
      labels(first: 30) { nodes { name } }
      files(first: 100) { nodes { path additions deletions } }
      commits(last: 100) { nodes { commit { oid messageHeadline
        statusCheckRollup { contexts(first: 100) { nodes {
          __typename
          ... on CheckRun { name status conclusion detailsUrl
                            checkSuite { workflowRun { workflow { name } } } }
          ... on StatusContext { context state targetUrl }
        } } } } } }
      reviews(last: 50) { nodes { state body author { login } } }
      headRepository { nameWithOwner }
    }
  }
}"""
PR_LIST_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { id number title url state isDraft mergeable autoMergeRequest { enabledAt } }
    }
  }
}"""
MERGE_METHODS = {"squash": "SQUASH", "merge": "MERGE", "rebase": "REBASE"}


def _pr_shape(p: dict) -> dict:
    """A GraphQL pull request in the shape `gh pr view --json` gives."""
    commits = (p.get("commits") or {}).get("nodes") or []
    last = (commits[-1].get("commit") if commits else {}) or {}
    checks = []
    for c in ((last.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes") or []:
        if c.get("__typename") == "CheckRun":
            wf = ((((c.get("checkSuite") or {}).get("workflowRun") or {}).get("workflow")) or {}).get("name")
            checks.append({"name": c.get("name"), "status": c.get("status"), "conclusion": c.get("conclusion"),
                           "detailsUrl": c.get("detailsUrl"), "workflowName": wf or ""})
        else:
            checks.append({"context": c.get("context"), "state": c.get("state"), "targetUrl": c.get("targetUrl")})
    out = {k: v for k, v in p.items() if k not in ("commits", "files", "labels", "reviews")}
    out["commits"] = [{"oid": (n.get("commit") or {}).get("oid", ""),
                       "messageHeadline": (n.get("commit") or {}).get("messageHeadline", "")} for n in commits]
    out["files"] = (p.get("files") or {}).get("nodes") or []
    out["labels"] = (p.get("labels") or {}).get("nodes") or []
    out["reviews"] = (p.get("reviews") or {}).get("nodes") or []
    out["statusCheckRollup"] = checks
    return out


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int, body: bytes = b""):
        super().__init__(message)
        self.status = status
        self.body = body


class HttpTransport:
    """GitHub's REST and GraphQL APIs with a token (stdlib only).

    Answers are cached in memory for this transport only (mason.cache): each resource has its own
    time to live, stale answers are revalidated with ETag / Last-Modified, writes made through
    this transport mark what they touch as stale, and the rate limit is watched so mason slows
    down (or answers from the cache) before GitHub says no.
    """

    name = "token"
    label = "GitHub"
    RATE_HEADERS = ("x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset")

    def __init__(self, token: str, api_url: str = "https://api.github.com", web: str = "https://github.com",
                 graphql_url: str | None = None, cache: str | None = None, timeout: int = 30,
                 user_agent: str = "mason"):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.graphql_url = graphql_url or (self.api_url + "/graphql")
        self.web = web.rstrip("/")
        self.cache_ttl = ttl_seconds(cache)
        self.timeout = timeout
        self.user_agent = user_agent
        self.caches = Spaces()
        self.store = Store()
        self.last_rate: dict = {}
        self.rates: dict[str, dict] = {}  # resource ("core", "graphql", "search"): remaining, limit, reset
        self.blocked: dict[str, float] = {}  # resource: time.time() until which Retry-After said wait
        self.stats = {"requests": 0, "cache_hits": 0, "not_modified": 0, "stale_served": 0, "invalidated": 0}

    def gh(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        raise RuntimeError(f"gh {' '.join(args[:3])}: not available here (no gh CLI with this login)")

    # plumbing

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _url(self, path: str) -> str:
        if path.startswith("https://"):
            raise RuntimeError("absolute URLs aren't allowed")
        return self.api_url + ("" if path.startswith("/") else "/") + path

    def _path(self, url: str) -> str:
        return url[len(self.api_url):] if url.startswith(self.api_url) else url

    def resource(self, url: str) -> str:
        if url == self.graphql_url:
            return "graphql"
        return "search" if self._path(url).startswith("/search/") else "core"

    def request(self, method: str, url: str, body: bytes | None = None, accept: str = "application/vnd.github+json",
                headers: dict | None = None) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Accept", accept)
        req.add_header("User-Agent", self.user_agent)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        for k, v in {**self._auth(), **(headers or {})}.items():
            req.add_header(k, v)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        self.stats["requests"] += 1
        res = self.resource(url)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (https only, see _url)
                data = resp.read()
                self._note_rate(resp.headers, res)
                return resp.status, dict(resp.headers), data
        except urllib.error.HTTPError as err:
            self._note_rate(err.headers, res)
            if err.code == 304:
                return 304, dict(err.headers or {}), b""
            data = err.read() or b""
            hdr = err.headers.get if err.headers else (lambda _k, _d=None: None)
            remaining = hdr(self.RATE_HEADERS[0])
            retry = hdr("retry-after")
            if err.code == 429 or (err.code == 403 and (remaining == "0" or retry or b"rate limit" in data.lower())):
                reset = hdr(self.RATE_HEADERS[2])
                when = int(reset) if reset and reset.isdigit() else None
                if retry and retry.isdigit():  # a secondary limit: wait that long before this kind of call again
                    self.blocked[res] = time.time() + int(retry)
                    when = int(time.time()) + int(retry)
                raise _rate_error(when, self.label) from err
            try:
                msg = json.loads(data).get("message") or ""
            except (ValueError, AttributeError):
                msg = data.decode(errors="replace")[:200]
            if isinstance(msg, (list, dict)):
                msg = json.dumps(msg)[:200]
            reason = {401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 422: "Unprocessable"}.get(err.code, "")
            raise HttpError(f"{method} {self._path(url)}: {msg or reason} (HTTP {err.code})", err.code, data) from err
        except urllib.error.URLError as err:
            raise RuntimeError(f"{method} {url}: {err.reason}") from err

    def _note_rate(self, headers, res: str = "core") -> None:
        if not headers:
            return
        rem, lim, reset = (headers.get(h) for h in self.RATE_HEADERS)
        if not (rem and lim and rem.isdigit() and lim.isdigit()):
            return
        res = headers.get("x-ratelimit-resource") or res
        rate = {"remaining": int(rem), "limit": int(lim), "reset": int(reset) if reset and reset.isdigit() else 0}
        self.rates[res] = rate
        if res == "core":
            self.last_rate = rate

    def limited(self, res: str = "core") -> int | None:
        """When this kind of call can't be made now: the time it can again (else None)."""
        now = time.time()
        if self.blocked.get(res, 0) > now:
            return int(self.blocked[res])
        r = self.rates.get(res)
        if r and r["remaining"] <= 0 and r["reset"] > now:
            return r["reset"]
        return None

    def pressure(self, res: str = "core") -> int:
        """How much longer cached answers are kept: 4x when under 5% of the limit is left."""
        r = self.rates.get(res)
        if r and r["limit"] and r["reset"] > time.time() and r["remaining"] < max(50, r["limit"] // 20):
            return 4
        return 1

    # the cache

    def _cached_get(self, url: str, accept: str, ttl: int, variant: str = "json"):
        """(status, headers, raw, entry) of a GET, from the cache when it is fresh (entry set on a hit)."""
        key = (url, variant, accept)
        e = self.store.get(key)
        res = self.resource(url)
        if e is not None and e.fresh():
            self.stats["cache_hits"] += 1
            return 200, e.extra, None, e
        blocked = self.limited(res)
        if blocked is not None:
            if e is not None:  # out of requests: an older answer beats none
                self.stats["stale_served"] += 1
                return 200, e.extra, None, e
            raise _rate_error(blocked, self.label)
        cond = {}
        if e is not None and e.etag:
            cond["If-None-Match"] = e.etag
        elif e is not None and e.modified:
            cond["If-Modified-Since"] = e.modified
        status, headers, raw = self.request("GET", url, accept=accept, headers=cond or None)
        ttl *= self.pressure(res)
        if status == 304 and e is not None:
            self.stats["not_modified"] += 1
            self.store.refresh(key, ttl)
            return 200, e.extra, None, e
        return status, headers, raw, None

    def _keep(self, url: str, variant: str, accept: str, value, raw: bytes, headers: dict, ttl: int) -> None:
        low = {k.lower(): v for k, v in headers.items()}
        cc = low.get("cache-control", "")
        if "no-store" in cc:
            return
        extra = {k: low[k.lower()] for k in ("Content-Type", "ETag", "Last-Modified") if k.lower() in low}
        self.store.put((url, variant, accept), value, len(raw), ttl * self.pressure(self.resource(url)),
                       etag=low.get("etag", ""), modified=low.get("last-modified", ""), extra=extra)

    def get_raw(self, path: str, accept: str = "application/vnd.github+json") -> tuple[int, dict, bytes]:
        """A GET's raw answer through the cache (the operator's GitHub proxy uses this)."""
        url = self._url(path)
        ttl = ttl_for(path, self.cache_ttl)
        status, headers, raw, hit = self._cached_get(url, accept, ttl, "raw")
        if hit is not None:
            return 200, dict(hit.extra), hit.value
        if 200 <= status < 300:
            self._keep(url, "raw", accept, raw, raw, headers, ttl)
        return status, headers, raw

    def forget(self, path: str = "") -> None:
        """A write went to `path`: mark what it can have changed as stale (all when it isn't about one repo)."""
        scope, repo = self._scope(path)
        if not scope:
            self.stats["invalidated"] += self.store.stale()
            self.caches.clear()
            return
        prefix = self._url(scope)
        graphql = self.graphql_url
        self.stats["invalidated"] += self.store.stale(prefix, also=lambda u: u == graphql)
        bare = path.split("?", 1)[0]
        self.caches.forget_repo(repo, repo_level=self._repo_level(bare, scope))
        if "/contents/" in bare or "/repository/" in bare or "/git/" in bare:
            self.caches.forget_files()

    def _scope(self, path: str) -> tuple[str, str]:
        """(the API path all of one repo's answers start with, the repo's name) of `path`."""
        parts = path.split("?", 1)[0].split("/")
        if path.startswith("/repos/") and len(parts) >= 4 and parts[2] and parts[3]:
            return "/".join(parts[:4]), f"{parts[2]}/{parts[3]}"
        return "", ""

    @staticmethod
    def _repo_level(bare: str, scope: str) -> bool:
        return bare == scope or bare.endswith(("/topics", "/generate", "/transfer"))

    def forget_graphql(self) -> None:
        """A mutation: every GraphQL answer and every status node may be out of date."""
        graphql = self.graphql_url
        self.stats["invalidated"] += self.store.stale(graphql, also=None)
        for name in ("nodes", "gl_nodes"):
            self.caches.pop(name, None)

    def graphql_raw(self, query: str, variables: dict) -> tuple[int, bytes]:
        """A GraphQL call's raw answer, queries through the cache (the operator's GitHub proxy uses this)."""
        body = json.dumps({"query": query, "variables": variables}).encode()
        mutation = query.lstrip().startswith("mutation")
        key = (self.graphql_url, "graphql-raw", body.decode())
        if not mutation:
            e = self.store.get(key)
            if e is not None and (e.fresh() or self.limited("graphql") is not None):
                self.stats["cache_hits" if e.fresh() else "stale_served"] += 1
                return 200, e.value
        try:
            status, _, raw = self.request("POST", self.graphql_url, body, accept="application/json")
        finally:
            if mutation:
                self.forget_graphql()
        if not mutation and status == 200 and b'"errors"' not in raw:
            self.store.put(key, raw, len(raw), graphql_ttl(self.cache_ttl) * self.pressure("graphql"))
        return status, raw

    def cache_info(self) -> dict:
        return {**self.stats, "entries": len(self.store), "bytes": self.store.bytes,
                "evictions": self.store.evictions}

    # the calls

    def api(self, method: str, path: str, body: dict | None = None):
        url = self._url(path)
        if method == "GET":
            ttl = ttl_for(path, self.cache_ttl)
            status, headers, raw, hit = self._cached_get(url, "application/vnd.github+json", ttl)
            if hit is not None:
                return hit.value
            value = json.loads(raw) if raw.strip() else None
            if 200 <= status < 300:
                self._keep(url, "json", "application/vnd.github+json", value, raw, headers, ttl)
            return value
        blocked = self.limited(self.resource(url))
        if blocked is not None:
            raise _rate_error(blocked, self.label)
        try:
            _, _, data = self.request(method, url, json.dumps(body).encode() if body is not None else None)
        finally:  # even a failed write may have changed something
            self.forget(path)
        return json.loads(data) if data.strip() else None

    def graphql(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        mutation = query.lstrip().startswith("mutation")
        key = (self.graphql_url, "graphql", body.decode())
        ttl = graphql_ttl(self.cache_ttl) * self.pressure("graphql")
        if not mutation:
            e = self.store.get(key)
            if e is not None and e.fresh():
                self.stats["cache_hits"] += 1
                return e.value
            blocked = self.limited("graphql")
            if blocked is not None:
                if e is not None:
                    self.stats["stale_served"] += 1
                    return e.value
                raise _rate_error(blocked, self.label)
        try:
            _, _, raw = self.request("POST", self.graphql_url, body, accept="application/json")
        finally:
            if mutation:
                self.forget_graphql()
        data = json.loads(raw)
        if data.get("errors"):
            raise RuntimeError("; ".join(e.get("message", "?") for e in data["errors"])[:300])
        if not mutation:
            self.store.put(key, data["data"], len(raw), ttl)
        return data["data"]

    def download(self, path: str) -> bytes:
        _, _, data = self.request("GET", self._url(path), accept="application/octet-stream")
        return data

    def rate_limit(self) -> dict:
        try:
            _, _, data = self.request("GET", self._url("/rate_limit"))
            return json.loads(data).get("resources") or {}
        except RuntimeError:
            return {}

    def logged_in(self) -> bool:
        try:
            self.api("GET", "/user")
            return True
        except RuntimeError:
            return False

    def exists(self, repo: str) -> bool:
        try:
            self.api("GET", f"/repos/{repo}")
            return True
        except HttpError as err:
            if err.status == 404:
                return False
            raise

    def _pr(self, repo: str, number: int) -> dict:
        owner, name = repo.split("/", 1)
        data = self.graphql(PR_QUERY, {"owner": owner, "name": name, "number": int(number)})
        p = ((data.get("repository") or {}).get("pullRequest"))
        if not p:
            raise RuntimeError(f"no pull request {repo}#{number}")
        return p

    def pr_view(self, repo: str, number: int, fields: str) -> dict:
        shaped = _pr_shape(self._pr(repo, number))
        return {k: shaped.get(k) for k in fields.split(",")}

    def pr_list_open(self, repo: str, fields: str) -> list[dict]:
        owner, name = repo.split("/", 1)
        data = self.graphql(PR_LIST_QUERY, {"owner": owner, "name": name})
        nodes = (((data.get("repository") or {}).get("pullRequests") or {}).get("nodes")) or []
        return [{k: n.get(k) for k in fields.split(",")} for n in nodes]

    def pr_diff(self, repo: str, number: int) -> str:
        _, _, data = self.request("GET", self._url(f"/repos/{repo}/pulls/{int(number)}"),
                                  accept="application/vnd.github.diff")
        return data.decode(errors="replace")

    def pr_ready(self, repo: str, number: int) -> None:
        pid = self._pr(repo, number)["id"]
        self.graphql("mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) "
                     "{ pullRequest { number } } }", {"id": pid})

    def pr_merge(self, repo: str, number: int, method: str = "squash", auto: bool = True) -> str:
        try:
            p = self._pr(repo, number)
            if auto:
                self.graphql("mutation($id: ID!, $m: PullRequestMergeMethod!) { enablePullRequestAutoMerge("
                             "input: {pullRequestId: $id, mergeMethod: $m}) { pullRequest { number } } }",
                             {"id": p["id"], "m": MERGE_METHODS.get(method, "SQUASH")})
                return ""
            self.api("PUT", f"/repos/{repo}/pulls/{int(number)}/merge", {"merge_method": method})
            head_repo = (p.get("headRepository") or {}).get("nameWithOwner")
            if head_repo == repo and p.get("headRefName"):
                with contextlib.suppress(RuntimeError):  # already deleted by the repo's own setting
                    self.api("DELETE", f"/repos/{repo}/git/refs/heads/{urllib.parse.quote(p['headRefName'])}")
            return ""
        except RuntimeError as err:
            return str(err)[:200]

    def pr_create(self, repo: str, base: str, head: str, title: str, body: str) -> str:
        got = self.api("POST", f"/repos/{repo}/pulls", {"title": title, "head": head, "base": base, "body": body})
        return (got or {}).get("html_url", "")

    def repo_create(self, repo: str, public: bool, template: str | None, description: str) -> None:
        owner, name = repo.split("/", 1)
        if template:
            self.api("POST", f"/repos/{template}/generate",
                     {"owner": owner, "name": name, "private": not public, "description": description})
            return
        body = {"name": name, "private": not public, "description": description, "auto_init": True}
        me = (self.api("GET", "/user") or {}).get("login", "")
        if owner.lower() == me.lower():
            self.api("POST", "/user/repos", body)
        else:
            self.api("POST", f"/orgs/{owner}/repos", body)


class OperatorTransport(HttpTransport):
    """An operator's GitHub proxy: the operator adds the user's own GitHub token on its side."""

    name = "operator"

    def __init__(self, url: str, key: str, cache: str | None = None):
        url = url.rstrip("/")
        super().__init__(key, api_url=f"{url}/api/v1/github/rest", graphql_url=f"{url}/api/v1/github/graphql",
                         cache=cache, user_agent="mason-cli")
        self.operator = url

    def get_json(self, path: str):
        """A GET on the operator's own API (/api/v1/...)."""
        _, _, data = self.request("GET", f"{self.operator}/api/v1{path}", accept="application/json")
        return json.loads(data) if data.strip() else None
