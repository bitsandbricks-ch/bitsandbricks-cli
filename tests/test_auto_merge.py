"""Auto-merge when GitHub has nothing to wait for (no required checks on the base branch)."""

from __future__ import annotations

import pytest

from mason import core
from mason import repos as R


class Stub:
    """A transport whose enablePullRequestAutoMerge answers with a canned GitHub error."""

    def __init__(self, auto_error: str = "", allowed: bool = True):
        self.auto_error, self.allowed, self.calls = auto_error, allowed, []

    def pr_merge(self, repo, number, method="squash", auto=True):
        self.calls.append("auto" if auto else "now")
        if auto and not self.allowed:
            return "Auto merge is not allowed for this repository"
        return self.auto_error if auto else ""

    def api(self, method, path, body=None):
        self.calls.append(f"{method} {path}")
        if method == "PATCH":
            self.allowed = True
        return {"allow_auto_merge": self.allowed}


@pytest.fixture
def stub(monkeypatch):
    def use(**kw):
        t = Stub(**kw)
        monkeypatch.setattr(core, "transport", lambda: t)
        return t
    return use


def test_auto_merge_on(stub):
    t = stub()
    assert R.auto_merge("acme/web", 7) == (False, "")
    assert t.calls == ["auto"]


def test_clean_pr_merges_now(stub):
    t = stub(auto_error="Pull request Pull request is in clean status")
    assert R.auto_merge("acme/web", 7) == (True, "")
    assert t.calls == ["auto", "now"]


def test_no_required_checks_says_why(stub):
    t = stub(auto_error="Pull request Pull request is in unstable status")
    merged, why = R.auto_merge("acme/web", 7)
    assert not merged and "requires none" in why and t.calls == ["auto"]


def test_repo_setting_off_is_turned_on(stub):
    t = stub(allowed=False)
    assert R.auto_merge("acme/web", 7) == (False, "")
    assert t.calls == ["auto", "GET /repos/acme/web", "PATCH /repos/acme/web", "auto"]


def test_merge_one_reports_merged_now(stub, capsys):
    stub(auto_error="Pull request is in clean status")
    p = {"number": 7, "title": "Fix", "url": "https://github.com/acme/web/pull/7", "state": "OPEN",
         "isDraft": False, "mergeable": "MERGEABLE", "autoMergeRequest": None}
    R.merge_one("acme/web", p, {}, named=True)
    assert "merged right away" in capsys.readouterr().out
