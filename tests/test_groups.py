"""Which repos a group takes: `match.org` for GitHub orgs and GitLab groups, and org filters (`acme/`)."""

from __future__ import annotations

import sys

import pytest

from mason import config
from mason.config import Config, Group, orgs_of


def cfg(*groups: Group) -> Config:
    return Config(source=None, orgs=["acme"], repos=[], exclude=[], groups=list(groups), org_defaults=False)


def test_match_org_ignores_case_on_github():
    g = Group("acme", match={"org": "Acme"})
    assert g.matches("acme/web") and g.matches("ACME/api")
    assert not g.matches("acme-labs/web") and not g.matches("other/acme")


def test_match_org_takes_gitlab_subgroups():
    top, sub = Group("top", match={"org": "grp"}), Group("sub", match={"org": "grp/sub"})
    assert top.matches("gitlab:grp/x") and top.matches("gitlab:grp/sub/y")
    assert sub.matches("gitlab:grp/sub/y") and sub.matches("gitlab:grp/sub/deeper/z")
    assert not sub.matches("gitlab:grp/x") and not sub.matches("gitlab:grp/subway/x")


def test_org_filter_takes_every_repo_of_the_org_grouped_or_not():
    c = cfg(Group("plugins", match={"org": "acme", "prefix": "plugin-"}))  # no catch-all: acme/web has no group
    assert c.group_of("acme/web") is None
    assert c.in_group("acme/web", "acme/") and c.in_group("acme/plugin-a", "acme/")
    assert c.in_group("Acme/web", "acme/")
    assert not c.in_group("mike/dotfiles", "acme/") and not c.in_group("gitlab:acme/x", "acme/")
    assert c.in_group("acme/plugin-a", "plugins") and not c.in_group("acme/web", "plugins")
    assert c.in_group("gitlab:grp/sub/p", "gitlab:grp/") and c.in_group("gitlab:grp/sub/p", "gitlab:grp/sub/")
    assert not c.in_group("gitlab:grp/p", "gitlab:grp/sub/") and not c.in_group("grp/p", "gitlab:grp/")


def test_group_names_stay_groups():
    c = cfg(Group("acme:default"), Group("rest"))
    assert c.in_group("acme/web", "acme:default") and not c.in_group("acme/web", "rest")
    assert config.org_filter("rest") is None and config.org_filter("/") is None


def test_orgs_of():
    assert orgs_of("acme/web") == ["acme/"]
    assert orgs_of("gitlab:a/b/p") == ["gitlab:a/", "gitlab:a/b/"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="the operator needs Python 3.10+")
def test_gitlab_group_filter_uses_topics(monkeypatch):
    pytest.importorskip("flask")
    pytest.importorskip("sqlalchemy")
    """A group that matches GitLab projects on topics keeps them when the list is filtered by it."""
    from flask import Flask

    from mason.server.api import repos as api

    c = cfg(Group("tagged", match={"provider": "gitlab", "topics": ["svc"]}))
    projects = {"gitlab:g/a": {"topics": ["svc"]}, "gitlab:g/b": {"topics": []}}
    monkeypatch.setattr(api, "gitlab_projects", lambda gl, cfg: projects)
    with Flask(__name__).test_request_context("/?provider=gitlab"):
        assert api.all_repos(c, None, object(), group="tagged")[1] == ["gitlab:g/a"]
        assert api.all_repos(c, None, object(), group="gitlab:g/")[1] == ["gitlab:g/a", "gitlab:g/b"]
