"""The operator's settings, all from the environment (see docs/mason.md)."""

from __future__ import annotations

import ipaddress
import os
import secrets
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from mason.cache import MB, size_of

GITHUB_SCOPES = "repo read:org workflow security_events read:packages"
# Mason's own GitHub OAuth app. A client ID is public (it is in every authorize URL), so it is safe to
# ship: with it anyone can sign in with GitHub's device flow, no client secret needed. GITHUB_CLIENT_ID
# overrides it, and with GITHUB_CLIENT_SECRET the usual redirect sign-in works too.
# Without a secret Mason can't revoke tokens at GitHub (oauth.revoke): users get the link to do it.
GITHUB_CLIENT_ID = "Ov23liFJgLtIjB3ZtuOy"
DEFAULT_PORT = 8080
LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa")
GITLAB_SCOPES = "api"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).replace(";", ",").split(",") if x.strip()]


def _who(entries: list[str]) -> set[tuple[str, str]]:
    """'github:mike', 'gitlab:ann' or a bare 'mike' (either provider) -> {(provider, login lowercased)}."""
    out = set()
    for e in entries:
        provider, _, login = e.partition(":") if ":" in e else ("*", "", e)
        out.add((provider.lower(), login.lower()))
    return out


@dataclass
class Provider:
    name: str           # github | gitlab
    client_id: str
    client_secret: str
    web: str            # https://github.com, https://gitlab.example.org
    api: str            # https://api.github.com, https://gitlab.example.org/api/v4
    scopes: str

    @property
    def redirect(self) -> bool:
        """The redirect sign-in (authorization code + PKCE) needs the app's client ID and secret."""
        return bool(self.client_id and self.client_secret)

    @property
    def device(self) -> bool:
        """GitHub's device flow needs only the client ID (the app must have device flow switched on)."""
        return self.name == "github" and bool(self.client_id)

    @property
    def enabled(self) -> bool:
        return self.redirect or self.device

    @property
    def method(self) -> str:
        """How the web app signs in with this provider: redirect, device or '' (off)."""
        return "redirect" if self.redirect else "device" if self.device else ""

    @property
    def host(self) -> str:
        return self.web.split("://", 1)[-1].rstrip("/")


@dataclass
class Settings:
    url: str
    data: Path
    database: str
    secret_key: str
    github: Provider
    gitlab: Provider
    allow_users: set = field(default_factory=set)
    allow_orgs: set = field(default_factory=set)
    admins: set = field(default_factory=set)
    idle_hours: int = 12
    session_days: int = 7
    cache: str = "60s"
    cache_size: int = 256 * 1024 * 1024  # all users' cached answers together, in memory only
    cache_user_size: int = 32 * 1024 * 1024  # one user's share
    plugin_index: str = ""
    plugin_index_keys: list = field(default_factory=list)
    plugin_install: bool = False
    trusted_proxies: int = 0
    audit_days: int = 90
    audit_rows: int = 1000
    audit_output: int = 1000
    audit_ips: str = "hash"
    rate_api: int = 600
    rate_writes: int = 60
    rate_anon: int = 120
    signups_per_hour: int = 60
    signups_per_ip: int = 5
    max_users: int = 1000
    max_keys: int = 50
    max_sessions: int = 20

    @property
    def secure_cookies(self) -> bool:
        return self.url.startswith("https://")

    @property
    def open_sign_in(self) -> bool:
        """No allow lists: anyone with a GitHub or GitLab account may sign in (and sees only their own)."""
        return not (self.allow_users or self.allow_orgs)

    def problems(self) -> list[str]:
        """Settings that would make the operator unsafe; it refuses to start with any of them."""
        out = []
        if not (self.github.enabled or self.gitlab.enabled):
            out.append("no login: set GITHUB_CLIENT_ID (and GITHUB_CLIENT_SECRET) and/or "
                       "GITLAB_CLIENT_ID/GITLAB_CLIENT_SECRET")
        parts = urllib.parse.urlsplit(self.url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            out.append(f"MASON_URL must be a full URL like https://mason.mortar.ch (got {self.url!r})")
        elif parts.scheme == "http" and not local_host(parts.hostname):
            out.append("MASON_URL must be https:// (plain http is only for localhost, private addresses and "
                       ".local, .lan or .home.arpa names)")
        if self.audit_ips not in ("hash", "none", "full"):
            out.append(f"MASON_AUDIT_IPS is hash, none or full (got {self.audit_ips!r})")
        if min(self.audit_days, self.audit_rows) < 1 or self.audit_output < 0:
            out.append("MASON_AUDIT_DAYS and MASON_AUDIT_ROWS must be at least 1, MASON_AUDIT_OUTPUT at least 0")
        if min(self.max_keys, self.max_sessions) < 1:
            out.append("MASON_MAX_KEYS and MASON_MAX_SESSIONS must be at least 1")
        return out


def local_host(host: str) -> bool:
    """localhost, a loopback, private or link-local address, or a .local/.lan/.home.arpa name."""
    host = host.lower().rstrip(".").strip("[]")
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def secret_key(data: Path) -> str:
    """MASON_SECRET_KEY, or a random key created once in the data folder (0600)."""
    env = _env("MASON_SECRET_KEY")
    if env:
        return env
    path = data / "secret.key"
    if path.exists():
        return path.read_text().strip()
    data.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key


def load(port: int | None = None) -> Settings:
    """The settings; without MASON_URL, the URL is http://localhost on `port` (mason serve --port)."""
    data = Path(_env("MASON_DATA", "data")).resolve()
    gl_web = _env("GITLAB_URL", "https://gitlab.com").rstrip("/")
    gh_web = _env("GITHUB_URL", "https://github.com").rstrip("/")
    public = gh_web == "https://github.com"
    gh_api = _env("GITHUB_API_URL") or ("https://api.github.com" if public else f"{gh_web}/api/v3")
    gh_id = _env("GITHUB_CLIENT_ID") or (GITHUB_CLIENT_ID if public else "")
    return Settings(
        url=(_env("MASON_URL") or f"http://localhost:{port or DEFAULT_PORT}").rstrip("/"),
        data=data,
        database=_env("DATABASE_URL") or f"sqlite:///{data / 'operator.db'}",
        secret_key=secret_key(data),
        github=Provider("github", gh_id, _env("GITHUB_CLIENT_SECRET"), gh_web, gh_api,
                        _env("GITHUB_SCOPES", GITHUB_SCOPES)),
        gitlab=Provider("gitlab", _env("GITLAB_CLIENT_ID"), _env("GITLAB_CLIENT_SECRET"), gl_web,
                        f"{gl_web}/api/v4", _env("GITLAB_SCOPES", GITLAB_SCOPES)),
        allow_users=_who(_list("MASON_ALLOW_USERS")),
        allow_orgs=_who(_list("MASON_ALLOW_ORGS")),
        admins=_who(_list("MASON_ADMINS")),
        idle_hours=int(_env("MASON_IDLE_HOURS", "12")),
        session_days=int(_env("MASON_SESSION_DAYS", "7")),
        cache=_env("MASON_CACHE", "60s"),
        cache_size=size_of(_env("MASON_CACHE_SIZE"), 256 * MB),
        cache_user_size=size_of(_env("MASON_CACHE_USER_SIZE"), 32 * MB),
        plugin_index=_env("MASON_PLUGIN_INDEX",
                          "https://raw.githubusercontent.com/bitsandbricks-ch/mason-cli/main/plugins/index.json"),
        plugin_index_keys=_list("MASON_PLUGIN_INDEX_KEYS"),
        plugin_install=_env("MASON_PLUGIN_INSTALL") == "1",
        trusted_proxies=int(_env("MASON_TRUSTED_PROXIES", "0")),
        audit_days=int(_env("MASON_AUDIT_DAYS", "90")),
        audit_rows=int(_env("MASON_AUDIT_ROWS", "1000")),
        audit_output=int(_env("MASON_AUDIT_OUTPUT", "1000")),
        audit_ips=_env("MASON_AUDIT_IPS", "hash").lower(),
        rate_api=int(_env("MASON_RATE_API", "600")),
        rate_writes=int(_env("MASON_RATE_WRITES", "60")),
        rate_anon=int(_env("MASON_RATE_ANON", "120")),
        signups_per_hour=int(_env("MASON_SIGNUPS_PER_HOUR", "60")),
        signups_per_ip=int(_env("MASON_SIGNUPS_PER_IP", "5")),
        max_users=int(_env("MASON_MAX_USERS", "1000")),
        max_keys=int(_env("MASON_MAX_KEYS", "50")),
        max_sessions=int(_env("MASON_MAX_SESSIONS", "20")),
    )
