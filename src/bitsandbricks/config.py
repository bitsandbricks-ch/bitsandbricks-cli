"""Where bnb looks, and what your repos should look like.

Two files:

* Your own config, `~/.config/bitsandbricks/config.toml` (`bnb config` shows it):

      defaults = "your-org/your-defaults"   # a GitHub repo, or a local folder
      ref = "main"                          # branch or tag of that repo
      orgs = ["another-org"]                # added to the orgs the defaults name

  BNB_DEFAULTS and BNB_ORGS (comma separated) override it for one run.

* `bitsandbricks.toml` in the defaults repo, shared by everyone who points bnb at it:

      orgs = ["your-org"]
      repos = ["someone/one-more-repo"]
      exclude = ["your-org/old-*"]

      [[group]]
      name = "plugins"
      match = { org = "your-org", prefix = "plugin-" }
      from = "your-org/plugin-template"     # live reference: its settings, rulesets, files
      files = [".gitignore", "LICENSE"]     # files that must match the reference
      require = { allow_auto_merge = true } # what the reference itself must have

      [[group]]
      name = "everything else"              # no match: takes the rest
      settings = { delete_branch_on_merge = true, has_wiki = false }
      security = { secret_scanning = "enabled" }
      immutable_releases = true
      rulesets = "rulesets/default"         # folder of ruleset JSON in the defaults repo
      release_assets = ["*.zip"]            # files every release must attach (`bnb releases`)
      version = "pyproject.toml"            # where the version lives (`bnb bump`, `bnb release`)
      changelog = ["CHANGELOG.md"]          # changelogs `bnb bump` adds the new section to
      files = "files/default"               # folder copied into each repo, path for path

  The first group that matches a repo is its group. Keys bnb doesn't know stay on
  the group for plugins (`group.extra`).
"""

from __future__ import annotations

import contextvars
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bitsandbricks import core
from bitsandbricks.core import api, file_on

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULTS_FILE = "bitsandbricks.toml"
REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITLAB_NAME = re.compile(r"^gitlab:[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$")
# The GitLab client of this context (the operator sets it): defaults repos named gitlab:group/project.
GITLAB: contextvars.ContextVar = contextvars.ContextVar("bnb_gitlab", default=None)
# A user's settings in place of the config file (the operator's web console sets it).
OVERRIDE: contextvars.ContextVar = contextvars.ContextVar("bnb_config_override", default=None)
GROUP_KEYS = {"name", "match", "from", "files", "require", "settings", "security", "immutable_releases",
              "vulnerability_reporting", "rulesets", "release_assets",
              "version", "changelog"}


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "bitsandbricks"


def config_file() -> Path:
    return Path(os.environ.get("BNB_CONFIG") or config_dir() / "config.toml")


def user_config() -> dict:
    path = config_file()
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as err:
        raise RuntimeError(f"{path}: {err}") from err


def save_user_config(values: dict) -> Path:
    """Write the user config (a flat table of strings and string lists)."""
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in values.items():
        if value is None or value == []:
            continue
        lines.append(f"{key} = {json.dumps(value)}")
    path.write_text("\n".join(lines) + "\n")
    return path


# ── the defaults repo ────────────────────────────────────────────────────────


class Source:
    """Read files from the defaults: a local folder, a GitHub repo or a GitLab project (gitlab:group/name) at a ref."""

    def __init__(self, where: str, ref: str | None = None, allow_local: bool = True):
        self.where = where
        self.ref = ref
        self.local = Path(os.path.expanduser(where)) if allow_local and _is_local(where) else None
        self.gitlab = where[len("gitlab:"):] if not self.local and GITLAB_NAME.match(where) else None
        if not self.local and not self.gitlab and not REPO_NAME.match(where):
            raise RuntimeError(f"Not a repo (owner/name) or a folder: {where}")

    def __str__(self) -> str:
        if self.local:
            return str(self.local)
        return f"{self.where}@{self.ref}" if self.ref else self.where

    def read(self, path: str) -> str | None:
        if self.local:
            f = self.local / path
            return f.read_text() if f.is_file() else None
        if self.gitlab:
            return self._gitlab().file(self.gitlab, path, self.ref)
        return file_on(self.where, path, self.ref)

    def _gitlab(self):
        gl = GITLAB.get()
        if gl is None:
            raise RuntimeError(f"{self.where}: GitLab defaults need a GitLab login (the operator)")
        return gl

    def tree(self, folder: str) -> list[str]:
        """Every file under `folder`, as paths relative to it."""
        folder = folder.strip("/")
        if self.local:
            root = self.local / folder
            if not root.is_dir():
                return []
            return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
        if self.gitlab:
            pre = folder + "/"
            return sorted(p[len(pre):] for p in self._gitlab().blobs(self.gitlab, self.ref) if p.startswith(pre))
        ref = self.ref or "HEAD"
        try:
            t = api("GET", f"/repos/{self.where}/git/trees/{ref}?recursive=1") or {}
        except RuntimeError:
            return []
        pre = folder + "/"
        return sorted(e["path"][len(pre):] for e in t.get("tree") or []
                      if e.get("type") == "blob" and e["path"].startswith(pre))


def _is_local(where: str) -> bool:
    return where.startswith((".", "/", "~")) or Path(where).is_dir()


@dataclass
class Group:
    name: str
    match: dict = field(default_factory=dict)
    reference: str | None = None  # `from`
    files: list[str] | str | None = None
    require: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    security: dict = field(default_factory=dict)
    immutable_releases: bool | None = None
    vulnerability_reporting: bool | None = None
    rulesets: str | None = None
    release_assets: list[str] = field(default_factory=list)  # globs every release must attach
    version: str | None = None  # the file that holds the version: plugin.yaml, pyproject.toml, VERSION ...
    changelog: list[str] = field(default_factory=list)  # changelogs that get a section per version
    extra: dict = field(default_factory=dict)

    def matches(self, repo: str, topics: list[str] | None = None) -> bool:
        m = self.match
        if not m:
            return True
        provider = "gitlab" if repo.startswith("gitlab:") else "github"
        if m.get("provider") and m["provider"] != provider:
            return False
        repo = repo[len("gitlab:"):] if provider == "gitlab" else repo
        owner, _, name = repo.partition("/")
        name = name.rsplit("/", 1)[-1]  # GitLab: group/subgroup/project
        orgs = _list(m.get("org"))
        if orgs and owner not in orgs:
            return False
        if m.get("prefix") and not name.startswith(m["prefix"]):
            return False
        if m.get("suffix") and not name.endswith(m["suffix"]):
            return False
        names = _list(m.get("repos"))
        if names and not any(_glob(repo, n) for n in names):
            return False
        if any(_glob(repo, n) for n in _list(m.get("exclude"))):
            return False
        wanted = _list(m.get("topics"))
        return not (wanted and not set(wanted) & set(topics or []))

    def alias(self, repo: str) -> str:
        """A short name for the repo inside its group: the prefix dropped (plugin-budgets -> budgets)."""
        name = repo.rsplit("/", 1)[-1] if repo.startswith("gitlab:") else repo.split("/", 1)[-1]
        prefix = self.match.get("prefix") or ""
        return name[len(prefix):] if prefix and name.startswith(prefix) and len(name) > len(prefix) else name


def _list(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _glob(repo: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(repo, pattern) if "/" in pattern else fnmatch.fnmatchcase(repo.split("/")[-1], pattern)


@dataclass
class Config:
    source: Source | None
    orgs: list[str]
    repos: list[str]
    exclude: list[str]
    groups: list[Group]
    raw: dict = field(default_factory=dict)

    def group_of(self, repo: str, topics: list[str] | None = None) -> Group | None:
        return next((g for g in self.groups if g.matches(repo, topics)), None)

    def excluded(self, repo: str) -> bool:
        return any(_glob(repo, p) for p in self.exclude)

    def rulesets_of(self, group: Group) -> dict[str, dict]:
        """The ruleset JSON files of a group's `rulesets` folder in the defaults repo, by ruleset name."""
        if not group.rulesets or not self.source:
            return {}
        out = {}
        for f in self.source.tree(group.rulesets):
            if not f.endswith(".json"):
                continue
            text = self.source.read(f"{group.rulesets.strip('/')}/{f}")
            try:
                rs = json.loads(text or "")
            except ValueError as err:
                raise RuntimeError(f"{self.source}: {group.rulesets}/{f} isn't valid JSON") from err
            out[rs.get("name") or f[:-5]] = rs
        return out

    def files_of(self, group: Group) -> dict[str, str]:
        """A group's `files` folder in the defaults repo: {path in the repo: text}."""
        if not isinstance(group.files, str) or not self.source:
            return {}
        root = group.files.strip("/")
        return {f: self.source.read(f"{root}/{f}") or "" for f in self.source.tree(root)}


def parse_defaults(text: str, where: str = DEFAULTS_FILE) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise RuntimeError(f"{where}: {err}") from err


def make_group(raw: dict, i: int) -> Group:
    return Group(
        name=raw.get("name") or f"group {i + 1}",
        match=raw.get("match") or {},
        reference=raw.get("from"),
        files=raw.get("files"),
        require=raw.get("require") or {},
        settings=raw.get("settings") or {},
        security=raw.get("security") or {},
        immutable_releases=raw.get("immutable_releases"),
        vulnerability_reporting=raw.get("vulnerability_reporting"),
        rulesets=raw.get("rulesets"),
        release_assets=_list(raw.get("release_assets")),
        version=raw.get("version"),
        changelog=_list(raw.get("changelog")),
        extra={k: v for k, v in raw.items() if k not in GROUP_KEYS},
    )


_loaded = core.Scoped("config")


def load(orgs: list[str] | None = None, mine: dict | None = None, env: bool = True) -> Config:
    """Your config plus the defaults repo's bitsandbricks.toml. `orgs` (from --org) narrows the orgs.
    The operator passes each user's settings as `mine` (env=False: no BNB_* variables, no local folders)."""
    if mine is None and OVERRIDE.get() is not None:
        mine, env = OVERRIDE.get(), False
    server = mine is not None
    if mine is None:
        mine = user_config()
        if not mine.get("defaults") and not os.environ.get("BNB_DEFAULTS"):
            mine = {**operator_settings(), **mine}
    getenv = os.environ.get if env else (lambda _k, _d=None: None)
    where = getenv("BNB_DEFAULTS") or mine.get("defaults")
    ref = getenv("BNB_DEFAULTS_REF") or mine.get("ref")
    key = (where, ref, tuple(orgs or ()), tuple(_list(mine.get("orgs"))), tuple(_list(mine.get("repos"))),
           tuple(_list(mine.get("exclude"))))
    if key in _loaded:
        return _loaded[key]
    raw: dict = {}
    source = None
    if where:
        source = Source(where, ref, allow_local=not server)
        text = source.read(DEFAULTS_FILE)
        if text is None:
            raise RuntimeError(f"No {DEFAULTS_FILE} in {source}. Start one with: bnb init {where}")
        raw = parse_defaults(text, f"{source}/{DEFAULTS_FILE}")
    env_orgs = [o.strip() for o in (getenv("BNB_ORGS") or "").split(",") if o.strip()]
    all_orgs = env_orgs or _unique(_list(raw.get("orgs")) + _list(mine.get("orgs")))
    cfg = Config(
        source=source,
        orgs=_unique(orgs) if orgs else all_orgs,
        repos=_unique(_list(raw.get("repos")) + _list(mine.get("repos"))),
        exclude=_list(raw.get("exclude")) + _list(mine.get("exclude")),
        groups=[make_group(g, i) for i, g in enumerate(raw.get("group") or [])],
        raw=raw,
    )
    _loaded[key] = cfg
    return cfg


def operator_settings() -> dict:
    """With `bnb login <operator>`: the defaults repo and orgs saved on the operator (no local config)."""
    from bitsandbricks.transport import OperatorTransport, current
    t = current()
    if not isinstance(t, OperatorTransport):
        return {}
    try:
        got = t.get_json("/settings") or {}
    except RuntimeError:
        return {}
    return {k: got[k] for k in ("defaults", "ref", "orgs", "repos", "exclude") if got.get(k)}


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


STARTER = """\
# bitsandbricks defaults: what the repos of these orgs should look like.
# Check: bnb doctor    Fix settings and rulesets: bnb doctor --fix    Files: bnb sync
orgs = [{orgs}]

# The first group that matches a repo is its group; a group without `match` takes the rest.
#
# [[group]]
# name = "plugins"
# match = {{ org = "your-org", prefix = "plugin-" }}
# from = "your-org/plugin-template"      # copy settings, rulesets and files from this repo
# files = [".gitignore", "LICENSE"]

[[group]]
name = "default"
settings = {{ delete_branch_on_merge = true, allow_auto_merge = true }}
security = {{ secret_scanning = "enabled", secret_scanning_push_protection = "enabled" }}
# rulesets = "rulesets/default"          # ruleset JSON files (export them from GitHub)
# files = "files/default"                # files every repo of the group gets
"""
