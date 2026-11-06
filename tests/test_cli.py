"""Every command against tests/fake_gh (a made-up org acme and user mike)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def mason(env, *args) -> str:
    proc = subprocess.run([sys.executable, "-m", "mason", *args], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60, check=False)
    return proc.stdout + proc.stderr


def test_repos_across_orgs_and_groups(env):
    out = mason(env, "repos")
    assert "acme/plugin-a" in out and "plugins  (a)" in out
    assert "mike/dotfiles" in out  # a user, not an org
    assert "acme/legacy" not in out and "acme/old" not in out  # excluded, archived
    assert "6 repos" in out
    assert "acme/web" not in mason(env, "repos", "--group", "plugins")
    assert "1 repos" in mason(env, "repos", "--org", "mike")


def test_names(env):
    assert "acme/plugin-b" in mason(env, "repos", "b")  # group alias
    assert "acme/api" in mason(env, "repos", "api")
    assert "Unknown repo: nope" in mason(env, "repos", "nope")


def test_status_is_one_query(env, tmp_path):
    log = tmp_path / "calls.log"
    out = mason({**env, "FAKE_GH_LOG": str(log)}, "status")
    assert "acme/api         default  v1.10.0   0/0              failure" in out
    assert "acme/web         default  -         2/1              success" in out
    assert sum("graphql" in line for line in log.read_text().splitlines()) == 1


def test_prs_and_pr(env):
    out = mason(env, "prs")
    assert "https://github.com/acme/web/pull/7" in out and "CONFLICTS" in out and "3 open PRs" in out
    out = mason(env, "pr", "web#7", "--diff")
    assert "blocked: required checks or reviews missing" in out
    assert "https://github.com/acme/web/actions/runs/1/job/2" in out
    assert "+print('hi')" in out


def test_merge(env):
    out = mason(env, "merge", "--dry-run", "web#8", "web#9", "api#3")
    assert "draft, left open (add --ready" in out
    assert "has conflicts, left open" in out
    assert "merged, nothing to do" in out
    out = mason(env, "merge", "--dry-run", "--ready", "--now", "web#8")
    assert "[dry-run] mark #8 WIP: dark mode ready for review" in out and "[dry-run] merge now #8" in out
    assert "auto-merge on (merges when checks are green)" in mason(env, "merge", "web#7")


def test_runs_approve_security(env):
    assert "actions/runs/9" in mason(env, "runs", "web")
    out = mason(env, "approve", "--dry-run")
    assert "waits for: production" in out and "approval to run (outside contributor)" in out
    out = mason(env, "security", "api", "dotfiles", "web")
    assert "high      Dependabot       picomatch: ReDoS  package-lock.json" in out
    assert "gh auth refresh -s security_events" in out
    assert "== acme/web\n   no open alerts\n" in out


def test_releases(env):
    out = mason(env, "releases")
    assert "plugin-a  v1.0.0" in out and "plugin-a-1.0.0.zip" in out
    assert "missing *.zip" in out  # plugin-b's group wants a zip
    assert "acme/plugin-b: missing *.zip; Release failure" in out
    assert 'sign: failure at step "cosign"' in out and "runs/7/job/8" in out
    assert "dotfiles" not in out  # no releases
    out = mason(env, "releases", "--problems")
    assert "plugin-b" in out and "plugin-a" not in out and "api" not in out


def test_packages(env):
    out = mason(env, "packages")
    assert "acme/api/backend  api   1.2.0" in out and "v1.10.0 missing" in out
    assert "acme/old" not in out  # its repo is excluded
    assert "== acme/web: Images failure" in out and 'at step "cosign"' in out
    assert "api/backend" not in mason(env, "packages", "web")


def test_rerun_and_blocked_approval(env, tmp_path):
    log = tmp_path / "calls.log"
    out = mason({**env, "FAKE_GH_LOG": str(log)}, "rerun", "plugin-b", "--yes")
    assert "failure  Release · v0.3.0" in out and 'failure    sign  (step "cosign")' in out
    assert "re-running the failed jobs of Release on v0.3.0" in out
    assert "POST /repos/acme/plugin-b/actions/runs/7/rerun-failed-jobs" in log.read_text()
    assert "no failed run" in mason(env, "rerun", "api", "--yes")
    assert "[dry-run] re-run" in mason(env, "rerun", "plugin-b", "--run", "7", "--dry-run")
    out = mason(env, "approve", "plugin-a", "--yes")
    assert 'you can\'t approve it: you started this run' in out and "Prevent self-review" in out


def test_bump_and_release(env, tmp_path):
    log = tmp_path / "calls.log"
    out = mason({**env, "FAKE_GH_LOG": str(log)}, "bump", "a", "minor")
    assert "1.0.0 -> 1.1.0 (plugin.yaml, CHANGELOG.md), 1 PR(s) merged since v1.0.0" in out
    assert "- Add search (#4)" in out and "Old fix" not in out
    assert "opened https://github.com/acme/plugin-a/pull/99" in out
    calls = log.read_text()
    assert "pr create --repo acme/plugin-a --base main --head bump/v1.1.0" in calls
    assert "PUT /repos/acme/plugin-a/contents/plugin.yaml" in calls
    assert "1.0.0 is not newer" in mason(env, "bump", "a", "1.0.0")
    rows = [line.split() for line in mason(env, "release").splitlines()]
    assert ["plugin-a", "plugin.yaml", "1.0.0", "v1.0.0"] in rows
    assert ["plugin-b", "plugin.yaml", "0.4.0", "v0.3.0", "v0.4.0"] in rows
    assert not any(r and r[0] == "api" for r in rows)  # its group names no version file
    out = mason(env, "release", "a")
    assert "v1.0.0 is released" in out
    out = mason(env, "release", "b")  # asks; no answer leaves it
    assert "left as it is" in out
    log.write_text("")
    out = mason({**env, "FAKE_GH_LOG": str(log)}, "release", "b", "--yes")
    assert "tagged v0.4.0 on main at headsha (was v0.3.0)" in out and "release run: queued" in out
    assert "api -X POST /repos/acme/plugin-b/git/refs" in log.read_text()


def test_version_files():
    from mason import versions as V
    assert V.read_version('[project]\nname = "x"\nversion = "0.2.0"\n', "app/pyproject.toml") == "0.2.0"
    assert V.write_version('{\n  "name": "x",\n  "version": "1.2.3"\n}\n', "package.json", "1.3.0") == \
        '{\n  "name": "x",\n  "version": "1.3.0"\n}\n'
    assert V.write_version("v2.0.1\n", "VERSION", "2.0.2") == "v2.0.2\n"
    assert V.next_version("1.2.3", "major") == "2.0.0" and V.next_version("1.2.3", "patch") == "1.2.4"
    text = V.with_changelog("# Changelog\n\n## [Unreleased]\n- New\n\n## [1.0.0] - 2026-01-01\n", "1.1.0",
                            "2026-11-05", [])
    assert "## [Unreleased]\n\n## [1.1.0] - 2026-11-05\n- New" in text
    assert V.with_changelog("## 1.1.0\n", "1.1.0", "x", []) is None
    assert V.with_changelog(None, "0.1.0", "x", ["- A (#1)"]) == "# Changelog\n\n## 0.1.0\n\n- A (#1)\n\n"


def test_run_and_pr_actions(env, monkeypatch, tmp_path):
    log = tmp_path / "calls.log"
    for key in ("PATH", "MASON_DEFAULTS"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    from mason import core, repos

    core.DRY_RUN = False
    lines = repos.failed_log("acme/plugin-b", 7)
    assert lines[0] == "── sign (last 2 lines)" and "Error: createLogEntryConflict (409)" in lines
    out = core.capture(repos.update_branch, "acme/web", 7)
    out += core.capture(repos.close_pr, "acme/web", 9)
    out += core.capture(repos.comment_pr, "acme/web", 7, "Looks good")
    out += core.capture(repos.cancel_run, "acme/web", {"id": 9, "name": "CI", "head_branch": "main"})
    calls = log.read_text()
    assert "PUT /repos/acme/web/pulls/7/update-branch" in calls and "PATCH /repos/acme/web/pulls/9 " in calls
    assert "POST /repos/acme/web/issues/7/comments" in calls
    assert "POST /repos/acme/web/actions/runs/9/cancel" in calls
    assert "closed acme/web#9" in out


def test_doctor(env):
    out = mason(env, "doctor")
    assert "== acme/plugin-tpl (the reference)\n   ok\n   reference of group plugins" in out
    assert "== acme/plugin-a\n   ok (group plugins)" in out
    b = out.split("== acme/plugin-b")[1].split("==")[0]
    assert "has_wiki is true, plugin-tpl has false" in b
    assert "ruleset main is missing" in b
    assert "release immutability is off, plugin-tpl has on" in b
    assert "LICENSE differs from plugin-tpl" in b
    assert "fix: mason doctor --fix acme/plugin-b" in b and "fix: mason sync acme/plugin-b" in b
    web = out.split("== acme/web")[1]
    assert "delete_branch_on_merge is false, defaults has true" in web
    assert "ruleset main is missing" in web and "SECURITY.md is missing" in web
    api = out.split("== acme/api")[1].split("==")[0]
    assert "ruleset release (.github/rulesets/release.json) isn't imported" in api
    assert "required_approving_review_count" not in api  # the defaults' ruleset leaves it out: any value is fine
    dot = out.split("== mike/dotfiles")[1].split("==")[0]
    assert "ruleset main: has required_linear_history, defaults doesn't" in dot


def test_doctor_fix_and_sync(env):
    out = mason(env, "doctor", "--fix", "--dry-run", "b", "web")
    assert "[dry-run] PATCH /repos/acme/plugin-b" in out
    assert "[dry-run] PUT /repos/acme/plugin-b/immutable-releases" in out
    assert "[dry-run] POST /repos/acme/plugin-b/rulesets" in out
    assert "[dry-run] POST /repos/acme/web/rulesets" in out
    assert "fixed 3" in out
    out = mason(env, "sync", "--dry-run")
    assert "== acme/plugin-b\n   update LICENSE\n   [dry-run] PR on mason/sync-headsha" in out
    assert "== acme/web\n   add SECURITY.md\n   [dry-run] PR on mason/sync-" in out
    assert "== acme/plugin-a\n   in sync with plugin-tpl" in out
    assert "== acme/api\n   in sync with defaults" in out
    assert "PR: https://github.com/acme/web/pull/99" in mason(env, "sync", "web")
    assert "not among the synced files: nope" in mason(env, "sync", "--only", "nope")


def test_local_defaults_and_init(env, tmp_path):
    folder = tmp_path / "defaults"
    e = {**env, "MASON_DEFAULTS": "", "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    out = mason(e, "init", str(folder), "--org", "mike")
    assert "wrote" in out and (folder / "mason.toml").exists()
    out = mason(e, "config")
    assert f"defaults     {folder}" in out and "orgs         mike" in out
    out = mason(e, "doctor")
    assert ("== mike/dotfiles\n  (group default)\n"
            "   - secret_scanning_push_protection is off, defaults has enabled") in out


def test_no_config(env, tmp_path):
    e = {**env, "MASON_DEFAULTS": "", "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    assert "No repos" in mason(e, "status")
    assert "dotfiles" in mason({**e, "MASON_ORGS": "mike"}, "repos")


def test_open_prints_link(env):
    assert "https://github.com/acme/web/pull/7" in mason(env, "open", "web#7")


def test_plugins(env, tmp_path):
    pkg = tmp_path / "demo_plugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('''
from mason.plugins import Plugin

class Demo(Plugin):
    name = "demo"
    description = "a test plugin"
    graphql_fields = 'extra: object(expression: "HEAD:x") { ... on Blob { text } }'

    def add_commands(self, sub):
        sub.add_parser("hello")
        return {"hello": lambda args: print("hello from demo")}

    def status_columns(self):
        return [("extra", lambda repo, node: (node.get("extra") or {}).get("text", "-"))]

    def doctor(self, repo, group, ctx):
        return ["demo says no"] if repo == "acme/web" else []

    def doctor_hints(self, repo, group, problems):
        return ["mason hello"] if "demo says no" in problems else []
''')
    dist = tmp_path / "demo_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: demo-plugin\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text("[mason.plugins]\ndemo = demo_plugin:Demo\n")
    e = {**env, "PYTHONPATH": str(tmp_path)}
    assert "demo             a test plugin" in mason(e, "plugins")
    assert "hello from demo" in mason(e, "hello")
    assert "from a plugin" in mason(e, "status", "web")
    out = mason(e, "doctor", "web")
    assert "- demo says no" in out and "fix: mason hello" in out


def test_core_helpers():
    from mason.core import git_blob_sha, newest

    assert git_blob_sha("hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"  # git hash-object
    assert newest(["v1.2.0", "v1.10.0", "v1.9.9"]) == "v1.10.0"


def test_readme_names_every_command():
    from mason.cli import ON_REPOS

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    for cmd in (*ON_REPOS, "pr", "open", "init", "config", "plugins", "tui", "new", "login", "logout", "whoami",
                "market"):
        assert f"mason {cmd}" in readme, cmd


def test_new_repo(env):
    out = mason(env, "new", "--dry-run", "acme/plugin-c")
    assert "== acme/plugin-c (group plugins)" in out
    assert "[dry-run] create private acme/plugin-c from the template acme/plugin-tpl" in out
    assert "[dry-run] PATCH /repos/acme/plugin-c" in out and "[dry-run] POST /repos/acme/plugin-c/rulesets" in out
    out = mason(env, "new", "--dry-run", "--public", "acme/site")
    assert "[dry-run] create public acme/site with a README" in out
    assert "[dry-run] PUT /repos/acme/site/contents/SECURITY.md" in out
    assert "exists already" in mason(env, "new", "acme/web")
    assert "No group 'nope'" in mason(env, "new", "acme/x", "--group", "nope")


def test_repo_checks_from_defaults(env, tmp_path):
    folder = tmp_path / "defaults"
    folder.mkdir()
    (folder / "mason.toml").write_text('''orgs = ["mike"]
[[group]]
name = "default"
security = { dependabot_alerts = "enabled" }
topics = ["dotfiles"]
labels = { bug = "d73a4a", docs = "#0075CA", security = { color = "b60205", description = "Security" } }
actions = { allowed_actions = "selected", default_workflow_permissions = "read" }
required_files = ["SECURITY.md", "CODEOWNERS|.github/CODEOWNERS"]
branch = "trunk"
visibility = "private"
description = true
skip = ["own_rulesets"]
''')
    e = {**env, "MASON_DEFAULTS": str(folder)}
    out = mason(e, "doctor", "dotfiles")
    for line in ("dependabot_alerts is disabled, defaults has enabled", "topics lack dotfiles",
                 "label bug is #ee0701, defaults has #d73a4a", "label security is missing",
                 "actions allowed_actions is all, defaults has selected",
                 "actions default_workflow_permissions is write, defaults has read",
                 "CODEOWNERS or .github/CODEOWNERS is missing", "default branch is main, defaults has trunk",
                 "it has no description"):
        assert line in out, line
    assert "label docs" not in out and "SECURITY.md is missing" not in out and "the repo is" not in out
    assert "required_linear_history" not in out  # skipped: own_rulesets
    out = mason(e, "doctor", "--fix", "--dry-run", "dotfiles")
    for call in ("PUT /repos/mike/dotfiles/vulnerability-alerts", "PUT /repos/mike/dotfiles/topics",
                 "PATCH /repos/mike/dotfiles/labels/bug", "POST /repos/mike/dotfiles/labels",
                 "PUT /repos/mike/dotfiles/actions/permissions",
                 "PUT /repos/mike/dotfiles/actions/permissions/workflow"):
        assert f"[dry-run] {call}" in out, call
    assert "default branch is main" in out  # checked only: --fix leaves it


def test_workflows(env, tmp_path):
    out = mason(env, "workflows", "web")
    web = out.split("== acme/web")[1]
    for line in ("ci.yml:9 actions/checkout@v4 isn't pinned to a commit",
                 "ci.yml:14 someone/thing@main isn't pinned to a commit",
                 "ci.yml:7 permissions: write-all", "ci.yml:1 no top-level permissions",
                 "ci.yml:11 pull_request_target (line 3) checks out the PR's code",
                 "ci.yml:15 github.event.pull_request.title goes straight into a shell",
                 "ci.yml:18 github.head_ref goes straight into a shell",
                 "fix: mason workflows --fix acme/web"):
        assert line in web, line
    assert "acme/tools" not in web and "local-action" not in web and "github.sha" not in web
    out = mason(env, "workflows", "--fix", "--dry-run", "web")
    assert "couldn't resolve someone/thing@main: left as it is" in out
    assert "[dry-run] PUT /repos/acme/web/contents/.github/workflows/ci.yml" in out
    assert "PR: " in out and "(pins 1 action in 1 workflow)" in out
    assert "ci.yml:14 someone/thing@main" in out and "ci.yml:9 actions/checkout" not in out.split("PR: ")[1]
    folder = tmp_path / "defaults"
    folder.mkdir()
    (folder / "mason.toml").write_text('orgs = ["acme"]\n[[group]]\nname = "default"\n'
                                        'workflows = { permissions = false, allow_unpinned = ["actions/*"] }\n')
    out = mason({**env, "MASON_DEFAULTS": str(folder)}, "doctor", "web").split("== acme/web")[1]
    assert "workflow ci.yml:14 someone/thing@main isn't pinned to a commit" in out
    assert "workflow ci.yml:18 github.head_ref goes straight into a shell" in out
    assert "actions/checkout" not in out and "permissions" not in out
    assert "fix: mason workflows --fix acme/web" in out


def test_workflow_pinning_keeps_the_tag():
    from mason import workflows

    text = "    steps:\n      - uses: actions/checkout@v4\n      - uses: 'actions/cache@v3' # old\n"
    workflows._resolved[("actions/checkout", "v4")] = "a" * 40
    workflows._resolved[("actions/cache", "v3")] = None
    new, changed, failed = workflows.pin(text, {"pinned": True, "allow_unpinned": []})
    assert f"      - uses: actions/checkout@{'a' * 40} # v4\n" in new
    assert "'actions/cache@v3' # old" in new and changed == ["actions/checkout@v4"] and failed == ["actions/cache@v3"]
    assert workflows.scan("x.yml", "permissions: {}\njobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n",
                          workflows.settings({"allow_unpinned": ["actions/*"]})) == []


def test_issues(env):
    out = mason(env, "issues", "web", "api")
    assert "== acme/web\n   #3     Login fails on Safari  [bug] @mike" in out
    assert "#4     Dark mode" in out and "Add login page" not in out  # PRs are left out
    assert "== acme/api\n   no issues" in out
    mine = mason(env, "issues", "--mine", "web")
    assert "#3" in mine and "#4" not in mine
    stale = mason(env, "issues", "--stale", "90", "web")
    assert "#3" not in stale and "#4" in stale
    assert "#4" not in mason(env, "issues", "--label", "bug", "web")
    out = mason(env, "issue", "view", "web#3")
    assert "#3 Login fails on Safari" in out and "Safari 18" in out and "I can reproduce it." in out
    out = mason(env, "issue", "new", "web", "Crash on start", "--body", "trace", "--label", "bug", "--dry-run")
    assert "[dry-run] POST /repos/acme/web/issues" in out
    out = mason(env, "issue", "close", "https://github.com/acme/web/issues/3", "--not-planned", "--dry-run")
    assert "[dry-run] PATCH /repos/acme/web/issues/3" in out and "closed acme/web#3" in out
    out = mason(env, "issue", "comment", "web#3", "ok", "--dry-run")
    assert "[dry-run] POST /repos/acme/web/issues/3/comments" in out
    assert "Name the issue" in mason(env, "issue", "close", "web")


def test_release_new_and_view(env, tmp_path):
    log = tmp_path / "calls.log"
    e = {**env, "FAKE_GH_LOG": str(log)}
    out = mason(e, "release", "new", "api", "v1.11.0", "--title", "Search", "--notes", "- New", "--draft", "--yes")
    assert "created the draft release v1.11.0 of acme/api from a new tag on main" in out
    assert "https://github.com/acme/api/releases/tag/v1.11.0" in out
    assert "api -X POST /repos/acme/api/releases" in log.read_text()
    assert "left as it is" in mason(env, "release", "new", "api", "v1.11.0")  # asks; no answer leaves it
    out = mason(env, "release", "new", "api", "v1.10.0", "--yes")
    assert "has a release v1.10.0 already" in out
    assert "not a tag name" in mason(env, "release", "new", "api", "bad tag", "--yes")
    out = mason(env, "release", "new", "web", "v0.1.0", "--prerelease", "--dry-run")
    assert "[dry-run] create the prerelease release v0.1.0 (v0.1.0) of acme/web" in out
    out = mason(env, "release", "view", "api")
    assert "v1.10.0 · published" in out and "by mike" in out and "Release v1.10.0" in out
    assert "- Faster startup" in out and "other releases" in out and "v1.2.0" in out and "prerelease" in out
    out = mason(env, "release", "view", "api", "v1.2.0")
    assert out.splitlines()[2].startswith("v1.2.0 · prerelease")
    assert "no release v9.9.9 in acme/api" in mason(env, "release", "view", "api", "v9.9.9")


def test_release_suggestion():
    from mason import releases as REL
    assert REL.suggest_from([]) == "v0.1.0"
    assert REL.suggest_from([{"tag_name": "v1.2.9"}, {"tag_name": "v1.10.0"}, {"tag_name": "v2.0.0", "draft": True}]) \
        == "v1.10.1"
    import pytest
    with pytest.raises(RuntimeError):
        REL.valid_tag("v1..2")


def test_release_tag_only_pushes_the_tag(env, tmp_path):
    log = tmp_path / "calls.log"
    out = mason({**env, "FAKE_GH_LOG": str(log)}, "release", "tag", "b", "v0.4.0", "--yes")
    assert "pushed the tag v0.4.0 on main of acme/plugin-b at headsha" in out
    assert ".github/workflows/release.yml creates the release" in out and "release run: queued" in out
    calls = log.read_text()
    assert "api -X POST /repos/acme/plugin-b/git/refs" in calls and "/releases --input" not in calls
    out = mason(env, "release", "tag", "api", "--dry-run")  # no tag given: the next patch
    assert "[dry-run] push the tag v1.10.1 on main of acme/api; no workflow here creates a release" in out
    assert "has a tag v1.0.0 already" in mason(env, "release", "tag", "a", "v1.0.0", "--yes")
    assert "left as it is" in mason(env, "release", "tag", "b", "v0.4.0")
    out = mason(env, "release", "tag", "b", "v0.5.0", "--yes")  # the version file decides the tag
    assert "plugin.yaml says 0.4.0, so the tag would be v0.4.0, not v0.5.0: bump it first" in out
    assert "push the tag v0.4.0 on main" in mason(env, "release", "tag", "b", "--dry-run")
