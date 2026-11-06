"""The in-memory cache of GitHub and GitLab answers (mason.cache and HttpTransport)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mason import cache, core
from mason.cache import MB, Budget, Spaces, Store, TTLDict, size_of, ttl_for
from mason.server.gitlab import GitLab
from mason.transport import HttpTransport


class Fake:
    """A tiny API: answers by path, ETags by content, logs every request with its headers."""

    def __init__(self):
        self.answers: dict[str, object] = {}
        self.headers: dict[str, str] = {}
        self.status: dict[str, tuple[int, dict]] = {}
        self.log: list[tuple[str, str, dict]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                fake.log.append((self.command, self.path, dict(self.headers)))
                if self.path in fake.status:
                    code, extra = fake.status[self.path]
                    self.send_response(code)
                    for k, v in {**fake.headers, **extra}.items():
                        self.send_header(k, v)
                    body = b'{"message": "API rate limit exceeded"}'
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                value = fake.answers.get(self.path, {"ok": True})
                if callable(value):
                    value = value(self.headers)
                body = json.dumps(value).encode()
                etag = f'"{abs(hash(body))}"'
                if self.command == "GET" and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    for k, v in fake.headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("ETag", etag)
                for k, v in fake.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _go

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def hits(self, path: str) -> list:
        return [x for x in self.log if x[1] == path]


@pytest.fixture
def api():
    f = Fake()
    yield f
    f.server.shutdown()


def gh(api, token="t1", ttl="60s"):
    return HttpTransport(token, api_url=api.url, graphql_url=api.url + "/graphql", cache=ttl)


# ── conditional requests ─────────────────────────────────────────────────────


def test_a_stale_answer_is_revalidated_with_its_etag(api):
    t = gh(api, ttl="0")  # no time to live: every read asks, but with If-None-Match
    api.answers["/repos/a/b"] = {"name": "b"}
    assert t.api("GET", "/repos/a/b") == {"name": "b"}
    assert t.api("GET", "/repos/a/b") == {"name": "b"}
    first, second = api.hits("/repos/a/b")
    assert "If-None-Match" not in first[2]
    assert second[2]["If-None-Match"].startswith('"')
    assert t.stats["not_modified"] == 1


def test_a_fresh_answer_needs_no_request_and_each_resource_has_its_own_ttl(api):
    t = gh(api)
    t.api("GET", "/repos/a/b/pulls?state=open")
    t.api("GET", "/repos/a/b/pulls?state=open")
    assert len(api.hits("/repos/a/b/pulls?state=open")) == 1
    assert t.stats["cache_hits"] == 1
    assert ttl_for("/repos/a/b/pulls?state=open", 60) == 30
    assert ttl_for("/repos/a/b/actions/runs?per_page=5", 60) == 30
    assert ttl_for("/repos/a/b/contents/README.md", 60) == 60
    assert ttl_for("/repos/a/b", 60) == 300
    assert ttl_for("/user", 60) == 300
    assert ttl_for("/rate_limit", 60) == 0
    assert ttl_for("/projects/g%2Fp/merge_requests?state=opened", 60) == 30
    assert ttl_for("/repos/a/b", 0) == 0


def test_rate_limit_is_never_cached(api):
    t = gh(api)
    t.api("GET", "/rate_limit")
    t.api("GET", "/rate_limit")
    assert len(api.hits("/rate_limit")) == 2


# ── writes ───────────────────────────────────────────────────────────────────


def test_a_write_makes_that_repos_answers_stale_but_not_other_repos(api):
    t = gh(api)
    t.api("GET", "/repos/a/b/labels")
    t.api("GET", "/repos/a/c/labels")
    t.caches.space("nodes")["a/b"] = {"x": 1}
    t.caches.space("nodes")["a/c"] = {"x": 2}
    t.api("POST", "/repos/a/b/labels", {"name": "bug"})
    t.api("GET", "/repos/a/b/labels")
    t.api("GET", "/repos/a/c/labels")
    assert len(api.hits("/repos/a/b/labels")) == 3  # GET, POST, GET again (revalidated)
    assert api.hits("/repos/a/b/labels")[-1][2].get("If-None-Match")
    assert len(api.hits("/repos/a/c/labels")) == 1
    assert "a/b" not in t.caches.space("nodes") and "a/c" in t.caches.space("nodes")


def test_a_repo_level_write_drops_the_repo_list_and_a_file_write_the_config(api):
    t = gh(api)
    t.caches.space("listing")["k"] = {"a/b": {}}
    t.caches.space("config")["k"] = "cfg"
    t.api("PUT", "/repos/a/b/contents/mason.toml", {"content": ""})
    assert "k" in t.caches.space("listing") and "k" not in t.caches.space("config")
    t.api("PUT", "/repos/a/b/topics", {"names": ["x"]})
    assert "k" not in t.caches.space("listing")


def test_a_mutation_makes_graphql_answers_stale(api):
    t = gh(api)
    api.answers["/graphql"] = {"data": {"viewer": {"login": "me"}}}
    t.graphql("query { viewer { login } }", {})
    t.graphql("query { viewer { login } }", {})
    assert len(api.hits("/graphql")) == 1
    t.caches.space("nodes")["a/b"] = {}
    t.graphql("mutation { x(input: {}) { y } }", {})
    t.graphql("query { viewer { login } }", {})
    assert len(api.hits("/graphql")) == 3
    assert "a/b" not in t.caches.space("nodes")


def test_a_write_to_a_gitlab_project_makes_only_that_project_stale(api):
    gl = GitLab("t", api_url=api.url, web=api.url, cache="60s")
    gl.api("GET", "/projects/g%2Fp/labels")
    gl.api("GET", "/projects/g%2Fq/labels")
    gl.caches.space("gl_nodes")["gitlab:g/p"] = {}
    gl.api("POST", "/projects/g%2Fp/labels", {"name": "x"})
    gl.api("GET", "/projects/g%2Fp/labels")
    gl.api("GET", "/projects/g%2Fq/labels")
    assert len(api.hits("/projects/g%2Fp/labels")) == 3
    assert len(api.hits("/projects/g%2Fq/labels")) == 1
    assert "gitlab:g/p" not in gl.caches.space("gl_nodes")


# ── rate limits ──────────────────────────────────────────────────────────────


def test_out_of_requests_serves_the_older_answer_and_refuses_new_ones_without_asking(api):
    t = gh(api, ttl="0")
    reset = str(int(time.time()) + 600)
    api.headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "5000", "X-RateLimit-Reset": reset}
    assert t.api("GET", "/repos/a/b") == {"ok": True}
    assert t.limited() == int(reset)
    asked = len(api.log)
    assert t.api("GET", "/repos/a/b") == {"ok": True}  # stale, but better than an error
    assert t.stats["stale_served"] == 1
    with pytest.raises(core.RateLimited):
        t.api("GET", "/repos/a/zzz")
    assert len(api.log) == asked  # neither asked GitHub


def test_retry_after_blocks_that_kind_of_call(api):
    t = gh(api)
    api.status["/repos/a/b"] = (403, {"Retry-After": "120"})
    with pytest.raises(core.RateLimited):
        t.api("GET", "/repos/a/b")
    asked = len(api.log)
    with pytest.raises(core.RateLimited):
        t.api("GET", "/repos/a/other")
    assert len(api.log) == asked
    api.answers["/graphql"] = {"data": {}}
    assert t.graphql("query { viewer { login } }", {}) == {}  # GraphQL is its own bucket


def test_low_on_requests_keeps_answers_longer(api):
    t = gh(api)
    api.headers = {"X-RateLimit-Remaining": "10", "X-RateLimit-Limit": "5000",
                   "X-RateLimit-Reset": str(int(time.time()) + 600)}
    t.api("GET", "/repos/a/b/contents/x")
    e = t.store.get((api.url + "/repos/a/b/contents/x", "json", "application/vnd.github+json"))
    assert e.fresh_until - time.monotonic() > 200  # 60s x 4


def test_gitlab_rate_headers_are_read(api):
    gl = GitLab("t", api_url=api.url, web=api.url)
    api.headers = {"RateLimit-Remaining": "7", "RateLimit-Limit": "2000", "RateLimit-Reset": "1"}
    gl.api("GET", "/projects/g%2Fp")
    assert gl.last_rate == {"remaining": 7, "limit": 2000, "reset": 1}
    api.status["/projects/g%2Fq"] = (429, {"Retry-After": "30"})
    with pytest.raises(core.RateLimited, match="GitLab"):
        gl.api("GET", "/projects/g%2Fq")


# ── per user ─────────────────────────────────────────────────────────────────


def test_two_users_never_see_each_others_answers(api):
    api.answers["/user"] = lambda h: {"login": h["Authorization"].split()[-1]}
    alice, bob = gh(api, "alice"), gh(api, "bob")
    assert alice.api("GET", "/user") == {"login": "alice"}
    assert bob.api("GET", "/user") == {"login": "bob"}
    assert alice.api("GET", "/user") == {"login": "alice"}
    assert bob.api("GET", "/user") == {"login": "bob"}
    assert alice.store is not bob.store and alice.caches is not bob.caches
    assert len(api.hits("/user")) == 2


# ── bounded memory ───────────────────────────────────────────────────────────


def test_one_store_stays_under_its_cap():
    b = Budget(max_bytes=10_000, per_store=1_000)
    s = Store(b)
    for i in range(20):
        s.put((f"u{i}", "json"), i, 100, ttl=60)
    assert s.bytes <= 1_000 and len(s) == 10
    assert s.get(("u0", "json")) is None and s.get(("u19", "json")).value == 19
    s.put(("big", "json"), "x", 5_000, ttl=60)  # bigger than a share: not kept at all
    assert s.get(("big", "json")) is None


def test_all_stores_together_stay_under_the_budget_oldest_first():
    b = Budget(max_bytes=1_000, per_store=1_000)
    one, two = Store(b), Store(b)
    for i in range(5):
        one.put((f"a{i}", "json"), i, 100, ttl=60)
    for i in range(8):
        two.put((f"b{i}", "json"), i, 100, ttl=60)
    assert b.total() <= 1_000
    assert one.get(("a0", "json")) is None and two.get(("b7", "json")) is not None


def test_a_dropped_transport_frees_its_share():
    b = Budget(max_bytes=1_000, per_store=1_000)
    s = Store(b)
    s.put(("a", "json"), 1, 500, ttl=60)
    assert b.total() == 500
    del s
    import gc
    gc.collect()
    assert b.total() == 0


def test_an_answer_that_cant_be_revalidated_is_dropped_when_stale():
    s = Store(None)
    s.put(("u", "graphql"), 1, 10, ttl=60)
    s.put(("v", "json"), 2, 10, ttl=60, etag='"x"')
    s.stale()
    assert s.get(("u", "graphql")) is None
    assert s.get(("v", "json")).value == 2 and not s.get(("v", "json")).fresh()


def test_ttldict_forgets_after_its_ttl(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: now[0])
    d = TTLDict(ttl=60, maxlen=3)
    d["a"] = 1
    assert d["a"] == 1 and "a" in d
    now[0] += 61
    assert "a" not in d and d.get("a") is None and len(d) == 0
    for k in "wxyz":
        d[k] = k
    assert list(d) == ["x", "y", "z"]


def test_spaces_have_per_name_ttls():
    s = Spaces()
    assert s.space("me").ttl == 600 and s.space("nodes").ttl == 60
    assert s.space("nodes") is s.space("nodes")


def test_sizes():
    assert size_of("256MB", 0) == 256 * MB
    assert size_of("64m", 0) == 64 * MB
    assert size_of("1g", 0) == 1024 * MB
    assert size_of("", 5) == 5 and size_of("lots", 5) == 5
    assert size_of("12", 0) == 12 * MB
