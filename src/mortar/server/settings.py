"""The operator's settings, all from the environment (see docs/mortar.md)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

GITHUB_SCOPES = "repo read:org workflow security_events read:packages"
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
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def host(self) -> str:
        return self.web.split("://", 1)[-1].rstrip("/")


@dataclass
class Settings:
    url: str
    data: Path
    database: str
    secret_key: str
    dev: bool
    github: Provider
    gitlab: Provider
    allow_users: set = field(default_factory=set)
    allow_orgs: set = field(default_factory=set)
    allow_anyone: bool = False
    admins: set = field(default_factory=set)
    idle_hours: int = 12
    session_days: int = 7
    cache: str = "60s"
    plugin_index: str = ""
    plugin_index_keys: list = field(default_factory=list)
    plugin_install: bool = False
    trusted_proxies: int = 0

    @property
    def secure_cookies(self) -> bool:
        return self.url.startswith("https://")

    def problems(self) -> list[str]:
        """Settings that would make the operator unsafe; it refuses to start with any of them."""
        out = []
        if not (self.github.enabled or self.gitlab.enabled):
            out.append("no login: set GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET and/or "
                       "GITLAB_CLIENT_ID/GITLAB_CLIENT_SECRET")
        if not (self.allow_users or self.allow_orgs or self.allow_anyone or self.admins):
            out.append("nobody may sign in: set MTR_ALLOW_USERS, MTR_ALLOW_ORGS or "
                       "MTR_ADMINS (or MTR_ALLOW_ANYONE=1 on purpose)")
        if not self.dev and not self.secure_cookies:
            out.append("MTR_URL must be https:// (MTR_DEV=1 allows http for local development)")
        return out


def secret_key(data: Path) -> str:
    """MTR_SECRET_KEY, or a random key created once in the data folder (0600)."""
    env = _env("MTR_SECRET_KEY")
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


def load() -> Settings:
    data = Path(_env("MTR_DATA", "data")).resolve()
    gl_web = _env("GITLAB_URL", "https://gitlab.com").rstrip("/")
    gh_web = _env("GITHUB_URL", "https://github.com").rstrip("/")
    public = gh_web == "https://github.com"
    gh_api = _env("GITHUB_API_URL") or ("https://api.github.com" if public else f"{gh_web}/api/v3")
    dev = _env("MTR_DEV") == "1"
    return Settings(
        url=_env("MTR_URL", "http://localhost:8080" if dev else "").rstrip("/"),
        data=data,
        database=_env("DATABASE_URL") or f"sqlite:///{data / 'operator.db'}",
        secret_key=secret_key(data),
        dev=dev,
        github=Provider("github", _env("GITHUB_CLIENT_ID"), _env("GITHUB_CLIENT_SECRET"), gh_web, gh_api,
                        _env("GITHUB_SCOPES", GITHUB_SCOPES)),
        gitlab=Provider("gitlab", _env("GITLAB_CLIENT_ID"), _env("GITLAB_CLIENT_SECRET"), gl_web,
                        f"{gl_web}/api/v4", _env("GITLAB_SCOPES", GITLAB_SCOPES)),
        allow_users=_who(_list("MTR_ALLOW_USERS")),
        allow_orgs=_who(_list("MTR_ALLOW_ORGS")),
        allow_anyone=_env("MTR_ALLOW_ANYONE") == "1",
        admins=_who(_list("MTR_ADMINS")),
        idle_hours=int(_env("MTR_IDLE_HOURS", "12")),
        session_days=int(_env("MTR_SESSION_DAYS", "7")),
        cache=_env("MTR_CACHE", "60s"),
        plugin_index=_env("MTR_PLUGIN_INDEX",
                          "https://raw.githubusercontent.com/bitsandbricks-ch/bitsandbricks-cli/main/plugins/index.json"),
        plugin_index_keys=_list("MTR_PLUGIN_INDEX_KEYS"),
        plugin_install=_env("MTR_PLUGIN_INSTALL") == "1",
        trusted_proxies=int(_env("MTR_TRUSTED_PROXIES", "0")),
    )
