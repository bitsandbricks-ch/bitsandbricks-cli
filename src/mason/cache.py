"""In-memory caches for GitHub and GitLab answers.

Every transport (one per user and token, see server/oauth.py) owns its own Store and its own
Spaces, so one user's answers are never looked up for another: there is no shared key space
to collide in. Nothing here is ever written to disk.

Store      raw API answers (REST GETs and GraphQL queries) with their ETag / Last-Modified and
           a per-resource time to live. A stale entry is revalidated with If-None-Match or
           If-Modified-Since, so a 304 costs no rate limit on GitHub.
Budget     one byte budget shared by all stores: when it is full, the least recently used
           entry of any store goes (each store also has its own cap, so one user can't
           push everyone else out).
Spaces     small derived caches (who am I, the repo list, GraphQL nodes ...), each a TTLDict
           with its own time to live and entry cap.
"""

from __future__ import annotations

import re
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import MutableMapping
from dataclasses import dataclass, field

MB = 1024 * 1024

# ── time to live per resource ────────────────────────────────────────────────
#
# "live" is never served from the cache without asking (ETags still make that cheap), "hot" is
# what changes all the time (PRs, CI), "warm" the files and settings of a repo, "cold" the
# account, its orgs and repo metadata. `base` is MASON_CACHE (60s by default).

LIVE = [r"/rate_limit$", r"/actions/runs/\d+/(jobs|logs)", r"/jobs/\d+/trace$"]
HOT = [r"/pulls", r"/issues", r"/merge_requests", r"/actions/runs", r"/check-runs", r"/check-suites",
       r"/status(es)?$", r"/commits/[^/]+/status", r"/pipelines", r"/jobs", r"/deployments",
       r"/approvals", r"/notes", r"/pending_deployments", r"/commits(\?|$)", r"/repository/commits"]
COLD = [r"^/user$", r"^/user/orgs", r"^/user/memberships", r"^/orgs/[^/]+$", r"^/users/[^/]+$",
        r"^/namespaces/", r"^/groups/[^/]+$", r"^/repos/[^/]+/[^/]+$", r"^/projects/[^/]+$"]
_LIVE, _HOT, _COLD = ([re.compile(p) for p in ps] for ps in (LIVE, HOT, COLD))


def ttl_for(path: str, base: int) -> int:
    """Seconds an answer for `path` (relative to the API root, query allowed) is served without asking."""
    if base <= 0:
        return 0
    bare = path.split("?", 1)[0]
    if any(p.search(bare) for p in _LIVE):
        return 0
    if any(p.search(path) for p in _HOT):
        return min(30, base)
    if any(p.search(bare) for p in _COLD):
        return base * 5
    return base


def graphql_ttl(base: int) -> int:
    """GraphQL queries ask for PR and CI state: as short as the hot REST resources."""
    return min(30, base) if base > 0 else 0


# ── the byte budget ──────────────────────────────────────────────────────────


class Budget:
    """All stores together stay under max_bytes; the oldest entry anywhere is evicted first."""

    def __init__(self, max_bytes: int = 256 * MB, per_store: int = 32 * MB):
        self.max_bytes = max_bytes
        self.per_store = per_store
        self.stores: weakref.WeakSet = weakref.WeakSet()
        self.lock = threading.Lock()

    def configure(self, max_bytes: int, per_store: int) -> None:
        self.max_bytes = max(0, max_bytes)
        self.per_store = max(0, min(per_store, self.max_bytes))
        self.enforce()

    def total(self) -> int:
        return sum(s.bytes for s in list(self.stores))

    def enforce(self) -> None:
        """Evict until the total fits (called without any store's lock held)."""
        with self.lock:
            while True:
                stores = [s for s in list(self.stores) if s.bytes]
                if sum(s.bytes for s in stores) <= self.max_bytes:
                    return
                victim = min(stores, key=lambda s: s.oldest_use())
                if not victim.evict_oldest():
                    return


BUDGET = Budget()


# ── one user's answers ───────────────────────────────────────────────────────


@dataclass
class Entry:
    value: object
    size: int
    fresh_until: float = 0.0
    etag: str = ""
    modified: str = ""
    used: float = field(default_factory=time.monotonic)
    extra: dict = field(default_factory=dict)

    def fresh(self, now: float | None = None) -> bool:
        return self.fresh_until > (time.monotonic() if now is None else now)


class Store:
    """One transport's cached answers. Keys are (url, variant) tuples: invalidation goes by url prefix."""

    def __init__(self, budget: Budget | None = BUDGET):
        self.entries: OrderedDict[tuple, Entry] = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()
        self.budget = budget
        self.evictions = 0
        if budget is not None:
            budget.stores.add(self)

    def __len__(self) -> int:
        return len(self.entries)

    def _cap(self) -> int:
        return self.budget.per_store if self.budget is not None else 32 * MB

    def get(self, key: tuple) -> Entry | None:
        with self.lock:
            e = self.entries.get(key)
            if e is not None:
                e.used = time.monotonic()
                self.entries.move_to_end(key)
            return e

    def put(self, key: tuple, value, size: int, ttl: float = 0, etag: str = "", modified: str = "",
            extra: dict | None = None) -> None:
        if not (ttl > 0 or etag or modified):
            return  # neither fresh for a while nor revalidatable: nothing to gain
        cap = self._cap()
        if size > cap:
            self.drop(key)
            return
        with self.lock:
            old = self.entries.pop(key, None)
            if old is not None:
                self.bytes -= old.size
            self.entries[key] = Entry(value, size, time.monotonic() + ttl if ttl > 0 else 0.0, etag, modified,
                                      extra=extra or {})
            self.bytes += size
            while self.bytes > cap and self.entries:
                _, gone = self.entries.popitem(last=False)
                self.bytes -= gone.size
                self.evictions += 1
        if self.budget is not None and self.budget.total() > self.budget.max_bytes:
            self.budget.enforce()

    def refresh(self, key: tuple, ttl: float) -> None:
        """A 304: the answer we have is still right."""
        with self.lock:
            e = self.entries.get(key)
            if e is not None:
                e.fresh_until = time.monotonic() + ttl if ttl > 0 else 0.0

    def drop(self, key: tuple) -> None:
        with self.lock:
            e = self.entries.pop(key, None)
            if e is not None:
                self.bytes -= e.size

    def stale(self, prefix: str = "", also=None) -> int:
        """Mark entries whose url starts with `prefix` (all when empty, plus those `also(url)` picks) as stale.
        They stay for revalidation: the next read asks the API, which answers 304 when nothing changed."""
        n = 0
        with self.lock:
            for (url, *_), e in self.entries.items():
                if not prefix or url.startswith(prefix) or (also is not None and also(url)):
                    if e.fresh_until:
                        n += 1
                    e.fresh_until = 0.0
                    if not (e.etag or e.modified):
                        e.fresh_until = -1.0  # can't be revalidated: drop below
            for k in [k for k, e in self.entries.items() if e.fresh_until < 0]:
                self.bytes -= self.entries.pop(k).size
        return n

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()
            self.bytes = 0

    def oldest_use(self) -> float:
        with self.lock:
            return next(iter(self.entries.values())).used if self.entries else float("inf")

    def evict_oldest(self) -> bool:
        with self.lock:
            if not self.entries:
                return False
            _, gone = self.entries.popitem(last=False)
            self.bytes -= gone.size
            self.evictions += 1
            return True


# ── derived caches ───────────────────────────────────────────────────────────

SPACE_TTL = {"me": 600, "listing": 300, "gl_listing": 300, "nodes": 60, "gl_nodes": 60, "config": 120,
             "org_defaults": 300, "workflows.resolved": 3600}
SPACE_MAX = 5000


class TTLDict(MutableMapping):
    """A dict whose entries vanish `ttl` seconds after they were set (oldest go first past `maxlen`)."""

    def __init__(self, ttl: float = 60, maxlen: int = SPACE_MAX):
        self.ttl = ttl
        self.maxlen = maxlen
        self._d: OrderedDict = OrderedDict()
        self._lock = threading.RLock()

    def _alive(self, k):
        hit = self._d.get(k)
        if hit is None:
            return None
        if self.ttl > 0 and hit[0] <= time.monotonic():
            self._d.pop(k, None)
            return None
        return hit

    def __getitem__(self, k):
        with self._lock:
            hit = self._alive(k)
            if hit is None:
                raise KeyError(k)
            return hit[1]

    def __setitem__(self, k, v):
        with self._lock:
            self._d.pop(k, None)
            self._d[k] = (time.monotonic() + self.ttl, v)
            while len(self._d) > self.maxlen:
                self._d.popitem(last=False)

    def __delitem__(self, k):
        with self._lock:
            del self._d[k]

    def __contains__(self, k) -> bool:
        with self._lock:
            return self._alive(k) is not None

    def __iter__(self):
        with self._lock:
            return iter([k for k in list(self._d) if self._alive(k) is not None])

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for k in list(self._d) if self._alive(k) is not None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


class Spaces(dict):
    """A transport's named derived caches: space(name) is a TTLDict with that name's time to live."""

    def space(self, name: str) -> TTLDict:
        got = self.get(name)
        if not isinstance(got, TTLDict):
            got = self[name] = TTLDict(SPACE_TTL.get(name, 60))
        return got

    def forget_repo(self, repo: str, repo_level: bool = False) -> None:
        """A write to `repo`: its status nodes go; repo-level writes also drop the repo lists."""
        for name in ("nodes", "gl_nodes"):
            if name in self:
                self.space(name).pop(repo, None)
        if repo_level:
            for name in ("listing", "gl_listing"):
                self.pop(name, None)

    def forget_files(self) -> None:
        """A file was written: a defaults repo, an org's .mason, or a repo's .mason.toml or version file."""
        self.pop("config", None)
        self.pop("org_defaults", None)
        self.pop("version_spec", None)


def size_of(text: str, default: int) -> int:
    """'256MB', '64m', '1g' or a plain number of MB."""
    t = (text or "").strip().lower().rstrip("b")
    if not t:
        return default
    unit = {"k": 1024, "m": MB, "g": 1024 * MB}.get(t[-1:], MB)
    digits = t.rstrip("kmg")
    return int(digits) * unit if digits.isdigit() else default
