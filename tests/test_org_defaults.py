"""An org's (or GitLab group's) .mason repo: precedence, per-user lookups and caching."""

from __future__ import annotations

import base64

import pytest

from mason import config, core
from mason.cache import Spaces

MINE = """
[[group]]
name = "plugins"
match = { prefix = "plugin-" }
settings = { has_wiki = false }

[[group]]
name = "rest"
settings = { delete_branch_on_merge = true }
"""

ORG = """
[[group]]
name = "services"
match = { prefix = "svc-" }
rulesets = "rulesets"

[[group]]
name = "default"
settings = { allow_auto_merge = true }
files = "files"
"""


class Gh:
    """A user's GitHub transport: answers /contents of the repos this user may read, counts calls."""

    def __init__(self, files: dict[str, dict[str, str]], fail: bool = False):
        self.files = files
        self.fail = fail
        self.calls: list[str] = []
        self.caches = Spaces()

    def api(self, method, path, body=None):
        self.calls.append(path)
        if self.fail:
            raise core.RateLimited("API rate limit exceeded")
        _, _, owner, name, kind, *rest = path.split("?")[0].split("/")
        repo, file = f"{owner}/{name}", "/".join(rest)
        if kind == "contents" and file in self.files.get(repo, {}):
            text = self.files[repo][file]
            return {"content": base64.b64encode(text.encode()).decode(), "sha": "0" * 40}
        if kind == "git" and repo in self.files:  # git/trees/HEAD?recursive=1
            return {"tree": [{"path": p, "type": "blob"} for p in self.files[repo]]}
        raise RuntimeError("gh: Not Found (HTTP 404)")


class Gl:
    def __init__(self, files: dict[str, dict[str, str]]):
        self.files = files
        self.calls: list[str] = []
        self.caches = Spaces()

    def file(self, repo, path, ref=None):
        self.calls.append(f"{repo}/{path}")
        return self.files.get(repo, {}).get(path)


def cfg_with(text: str = MINE, **mine) -> config.Config:
    raw = config.parse_defaults(text)
    return config.Config(source=None, orgs=["acme"], repos=[], exclude=[],
                         groups=[config.make_group(g, i) for i, g in enumerate(raw.get("group") or [])],
                         raw=raw, **mine)


@pytest.fixture
def acme():
    return Gh({"acme/.mason": {"mason.toml": ORG, "files/SECURITY.md": "Tell us.\n",
                               "rulesets/main.json": '{"name": "main"}'}})


def test_precedence(acme):
    cfg = cfg_with()
    with core.using(acme):
        assert cfg.group_of("acme/plugin-x").name == "plugins"  # your explicit group wins
        assert cfg.group_of("acme/svc-pay").name == "acme:services"  # then the org's .mason
        assert cfg.group_of("acme/web").name == "acme:default"  # its catch-all beats yours
        assert cfg.group_of("other/web").name == "rest"  # no .mason in that org: your catch-all
        assert cfg.group_of("acme/web").origin == "acme/.mason"


def test_rulesets_and_files_come_from_the_org_repo(acme):
    cfg = cfg_with()
    with core.using(acme):
        g = cfg.group_of("acme/web")
        assert cfg.files_of(g) == {"SECURITY.md": "Tell us.\n"}
        assert cfg.rulesets_of(cfg.group_of("acme/svc-pay")) == {"main": {"name": "main"}}
        assert cfg.find_group("acme:services", "acme/new") is not None
        assert cfg.find_group("services", "acme/new") is None
        assert [g.name for g in cfg.groups_for(["acme/a", "acme/b"])] == [
            "plugins", "rest", "acme:services", "acme:default"]


def test_off(acme):
    cfg = cfg_with(org_defaults=False)
    with core.using(acme):
        assert cfg.group_of("acme/web").name == "rest"
    assert acme.calls == []


def test_off_switch_in_settings_and_env(monkeypatch):
    monkeypatch.setattr(config, "_loaded", {})
    assert config.load(mine={"org_defaults": False}, env=False).org_defaults is False
    assert config.load(mine={}, env=False).org_defaults is True
    monkeypatch.setenv("MASON_ORG_DEFAULTS", "off")
    assert config.load(mine={}, env=True).org_defaults is False


def test_each_user_reads_with_their_own_login(acme):
    """Ann can read acme/.mason; Bob can't (private repo): Bob never sees Ann's answer."""
    bob = Gh({})
    cfg = cfg_with()
    with core.using(acme):
        assert cfg.group_of("acme/web").name == "acme:default"
    with core.using(bob):
        assert cfg.group_of("acme/web").name == "rest"
    assert bob.calls == ["/repos/acme/.mason/contents/mason.toml"]
    assert "org_defaults" in acme.caches and acme.caches["org_defaults"] is not bob.caches.get("org_defaults")


def test_cached_per_user(acme):
    cfg = cfg_with()
    with core.using(acme):
        for repo in ("acme/web", "acme/api", "acme/svc-x"):
            cfg.group_of(repo)
        assert acme.calls.count("/repos/acme/.mason/contents/mason.toml") == 1
        acme.caches.forget_files()  # a file written through mason: read it again
        cfg.group_of("acme/web")
        assert acme.calls.count("/repos/acme/.mason/contents/mason.toml") == 2


def test_a_missing_repo_is_cached_but_an_outage_isnt():
    nobody = Gh({})
    with core.using(nobody):
        cfg_with().group_of("acme/web")
        cfg_with().group_of("acme/api")
    assert len(nobody.calls) == 1
    down = Gh({}, fail=True)
    with core.using(down):
        assert cfg_with().group_of("acme/web").name == "rest"
        cfg_with().group_of("acme/api")
    assert len(down.calls) == 2


def test_a_broken_org_file_says_so(acme):
    acme.files["acme/.mason"]["mason.toml"] = "[[group]\nname ="
    with core.using(acme), pytest.raises(RuntimeError, match="acme/.mason/mason.toml"):
        cfg_with().group_of("acme/web")


def test_gitlab_nearest_group_first():
    gl = Gl({"top/sub/.mason": {"mason.toml": '[[group]]\nname = "sub"\nmatch = { prefix = "svc-" }\n'},
             "top/.mason": {"mason.toml": '[[group]]\nname = "top"\n'}})
    assert config.org_candidates("gitlab:top/sub/svc-a") == ["gitlab:top/sub/.mason", "gitlab:top/.mason"]
    token = config.GITLAB.set(gl)
    try:
        with core.using(Gh({})):
            cfg = cfg_with("")
            assert cfg.group_of("gitlab:top/sub/svc-a").name == "top/sub:sub"
            assert cfg.group_of("gitlab:top/sub/web").name == "top:top"  # the parent's
            assert cfg.group_of("gitlab:else/web") is None
    finally:
        config.GITLAB.reset(token)
    # without a GitLab login nothing is looked up
    with core.using(Gh({})):
        assert cfg_with("").group_of("gitlab:top/web") is None
