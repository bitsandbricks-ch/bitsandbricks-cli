"""Running mason's code for one user: their GitHub transport, their GitLab client and their settings.

Everything here is per request (contextvars), so two users' commands never share a token,
a cache, a dry run setting or output.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

from flask import abort, g

from mason import config, core
from mason.server import oauth

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITLAB_REPO = re.compile(r"^gitlab:[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$")
SETTING_KEYS = {"defaults": str, "ref": str, "orgs": list, "repos": list, "exclude": list,
                "gitlab_groups": list, "gitlab_repos": list, "theme": str, "plugins_off": list}


class NoTransport:
    """Stands in when a user has no GitHub login: the operator never falls back to a server-side gh."""

    name = "none"
    web = "https://github.com"

    def __init__(self, why: str = "connect GitHub first (Settings › Accounts)"):
        self.why = why
        self.caches: dict = {}

    def __getattr__(self, name):
        def refuse(*_a, **_kw):
            raise RuntimeError(self.why)
        return refuse


def valid_repo(value: str) -> str:
    value = (value or "").strip()
    if not (REPO.match(value) or GITLAB_REPO.match(value)):
        abort(400, f"not a repo name: {value[:80]!r}")
    return value


def user_settings(user) -> dict:
    s = user.settings
    return {k: s.get(k, [] if t is list else "") for k, t in SETTING_KEYS.items()}


def clean_settings(body: dict) -> dict:
    out = {}
    for k, t in SETTING_KEYS.items():
        if k not in body:
            continue
        v = body[k]
        if t is list:
            if isinstance(v, str):
                v = [x.strip() for x in v.replace("\n", ",").split(",")]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                abort(400, f"{k} must be a list of names")
            v = [x.strip() for x in v if x.strip()][:200]
            if any(len(x) > 200 or "\x00" in x for x in v):
                abort(400, f"{k}: a name is too long")
        else:
            if not isinstance(v, str) or len(v) > 300:
                abort(400, f"{k} must be a short text")
            v = v.strip()
        out[k] = v
    if out.get("defaults") and not (REPO.match(out["defaults"]) or GITLAB_REPO.match(out["defaults"])):
        abort(400, "defaults must be a repo (owner/name) or a GitLab project (gitlab:group/name)")
    return out


@contextmanager
def as_user(dry: bool | None = None):
    """mason's code runs inside this with the signed-in user's GitHub token and GitLab client."""
    user = g.user
    try:
        gh = oauth.github(user) or NoTransport()
        gl = oauth.gitlab(user)
    except RuntimeError as err:
        abort(409, str(err))
    gtoken = config.GITLAB.set(gl)
    try:
        with core.using(gh, dry):
            yield gh, gl
    finally:
        config.GITLAB.reset(gtoken)


def load_config(orgs: list[str] | None = None):
    """This user's config: their settings plus their defaults repo (inside as_user)."""
    s = user_settings(g.user)
    mine = {k: s[k] for k in ("defaults", "ref", "orgs", "repos", "exclude") if s.get(k)}
    try:
        return config.load(orgs, mine=mine, env=False)
    except RuntimeError as err:
        abort(422, f"your defaults: {err}")


def gitlab_projects(gl, cfg) -> dict:
    from mason.server import gitlab as GL
    if gl is None:
        return {}
    s = user_settings(g.user)
    groups = s.get("gitlab_groups") or list((cfg.raw or {}).get("gitlab_groups") or [])
    extra = s.get("gitlab_repos") or []
    key = ("gl_projects", tuple(groups), tuple(extra), tuple(cfg.exclude))
    cache = gl.caches.setdefault("gl_listing", {})
    if key not in cache:
        cache[key] = GL.projects(gl, groups, extra, cfg.exclude)
    return cache[key]
