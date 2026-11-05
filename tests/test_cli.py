"""Every command against tests/fake_gh (a made-up org acme and user mike)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def bnb(env, *args) -> str:
    proc = subprocess.run([sys.executable, "-m", "bitsandbricks", *args], env=env, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=60, check=False)
    return proc.stdout + proc.stderr


def test_repos_across_orgs_and_groups(env):
    out = bnb(env, "repos")
    assert "acme/plugin-a" in out and "plugins  (a)" in out
    assert "mike/dotfiles" in out  # a user, not an org
    assert "acme/legacy" not in out and "acme/old" not in out  # excluded, archived
    assert "6 repos" in out
    assert "acme/web" not in bnb(env, "repos", "--group", "plugins")
    assert "1 repos" in bnb(env, "repos", "--org", "mike")


def test_names(env):
    assert "acme/plugin-b" in bnb(env, "repos", "b")  # group alias
    assert "acme/api" in bnb(env, "repos", "api")
    assert "Unknown repo: nope" in bnb(env, "repos", "nope")


def test_status_is_one_query(env, tmp_path):
    log = tmp_path / "calls.log"
    out = bnb({**env, "FAKE_GH_LOG": str(log)}, "status")
    assert "acme/api         default  v1.10.0   0/0              failure" in out
    assert "acme/web         default  -         2/1              success" in out
    assert sum("graphql" in line for line in log.read_text().splitlines()) == 1


def test_prs_and_pr(env):
    out = bnb(env, "prs")
    assert "https://github.com/acme/web/pull/7" in out and "CONFLICTS" in out and "3 open PRs" in out
    out = bnb(env, "pr", "web#7", "--diff")
    assert "blocked: required checks or reviews missing" in out
    assert "https://github.com/acme/web/actions/runs/1/job/2" in out
    assert "+print('hi')" in out


def test_merge(env):
    out = bnb(env, "merge", "--dry-run", "web#8", "web#9", "api#3")
    assert "draft, left open (add --ready" in out
    assert "has conflicts, left open" in out
    assert "merged, nothing to do" in out
    out = bnb(env, "merge", "--dry-run", "--ready", "--now", "web#8")
    assert "[dry-run] mark #8 WIP: dark mode ready for review" in out and "[dry-run] merge now #8" in out
    assert "auto-merge on (merges when checks are green)" in bnb(env, "merge", "web#7")


def test_runs_approve_security(env):
    assert "actions/runs/9" in bnb(env, "runs", "web")
    out = bnb(env, "approve", "--dry-run")
    assert "waits for: production" in out and "approval to run (outside contributor)" in out
    out = bnb(env, "security", "api", "dotfiles", "web")
    assert "high      Dependabot       picomatch: ReDoS  package-lock.json" in out
    assert "gh auth refresh -s security_events" in out
    assert "== acme/web\n   no open alerts\n" in out


def test_releases(env):
    out = bnb(env, "releases")
    assert "plugin-a  v1.0.0" in out and "plugin-a-1.0.0.zip" in out
    assert "missing *.zip" in out  # plugin-b's group wants a zip
    assert "acme/plugin-b: missing *.zip; Release failure" in out
    assert 'sign: failure at step "cosign"' in out and "runs/7/job/8" in out
    assert "dotfiles" not in out  # no releases
    out = bnb(env, "releases", "--problems")
    assert "plugin-b" in out and "plugin-a" not in out and "api" not in out


def test_packages(env):
    out = bnb(env, "packages")
    assert "acme/api/backend  api   1.2.0" in out and "v1.10.0 missing" in out
    assert "acme/old" not in out  # its repo is excluded
    assert "== acme/web: Images failure" in out and 'at step "cosign"' in out
    assert "api/backend" not in bnb(env, "packages", "web")


def test_doctor(env):
    out = bnb(env, "doctor")
    assert "== acme/plugin-tpl (the reference)\n   ok\n   reference of group plugins" in out
    assert "== acme/plugin-a\n   ok (group plugins)" in out
    b = out.split("== acme/plugin-b")[1].split("==")[0]
    assert "has_wiki is true, plugin-tpl has false" in b
    assert "ruleset main is missing" in b
    assert "release immutability is off, plugin-tpl has on" in b
    assert "LICENSE differs from plugin-tpl" in b
    assert "fix: bnb doctor --fix acme/plugin-b" in b and "fix: bnb sync acme/plugin-b" in b
    web = out.split("== acme/web")[1]
    assert "delete_branch_on_merge is false, defaults has true" in web
    assert "ruleset main is missing" in web and "SECURITY.md is missing" in web
    api = out.split("== acme/api")[1].split("==")[0]
    assert "ruleset release (.github/rulesets/release.json) isn't imported" in api
    assert "required_approving_review_count" not in api  # the defaults' ruleset leaves it out: any value is fine
    dot = out.split("== mike/dotfiles")[1].split("==")[0]
    assert "ruleset main: has required_linear_history, defaults doesn't" in dot


def test_doctor_fix_and_sync(env):
    out = bnb(env, "doctor", "--fix", "--dry-run", "b", "web")
    assert "[dry-run] PATCH /repos/acme/plugin-b" in out
    assert "[dry-run] PUT /repos/acme/plugin-b/immutable-releases" in out
    assert "[dry-run] POST /repos/acme/plugin-b/rulesets" in out
    assert "[dry-run] POST /repos/acme/web/rulesets" in out
    assert "fixed 3" in out
    out = bnb(env, "sync", "--dry-run")
    assert "== acme/plugin-b\n   update LICENSE\n   [dry-run] PR on bnb/sync-headsha" in out
    assert "== acme/web\n   add SECURITY.md\n   [dry-run] PR on bnb/sync-" in out
    assert "== acme/plugin-a\n   in sync with plugin-tpl" in out
    assert "== acme/api\n   in sync with defaults" in out
    assert "PR: https://github.com/acme/web/pull/99" in bnb(env, "sync", "web")
    assert "not among the synced files: nope" in bnb(env, "sync", "--only", "nope")


def test_local_defaults_and_init(env, tmp_path):
    folder = tmp_path / "defaults"
    e = {**env, "BNB_DEFAULTS": "", "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    out = bnb(e, "init", str(folder), "--org", "mike")
    assert "wrote" in out and (folder / "bitsandbricks.toml").exists()
    out = bnb(e, "config")
    assert f"defaults     {folder}" in out and "orgs         mike" in out
    out = bnb(e, "doctor")
    assert ("== mike/dotfiles\n  (group default)\n"
            "   - secret_scanning_push_protection is off, defaults has enabled") in out


def test_no_config(env, tmp_path):
    e = {**env, "BNB_DEFAULTS": "", "XDG_CONFIG_HOME": str(tmp_path / "cfg")}
    assert "No repos" in bnb(e, "status")
    assert "dotfiles" in bnb({**e, "BNB_ORGS": "mike"}, "repos")


def test_open_prints_link(env):
    assert "https://github.com/acme/web/pull/7" in bnb(env, "open", "web#7")


def test_plugins(env, tmp_path):
    pkg = tmp_path / "demo_plugin"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('''
from bitsandbricks.plugins import Plugin

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
        return ["bnb hello"] if "demo says no" in problems else []
''')
    dist = tmp_path / "demo_plugin-0.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: demo-plugin\nVersion: 0.1\n")
    (dist / "entry_points.txt").write_text("[bitsandbricks.plugins]\ndemo = demo_plugin:Demo\n")
    e = {**env, "PYTHONPATH": str(tmp_path)}
    assert "demo             a test plugin" in bnb(e, "plugins")
    assert "hello from demo" in bnb(e, "hello")
    assert "from a plugin" in bnb(e, "status", "web")
    out = bnb(e, "doctor", "web")
    assert "- demo says no" in out and "fix: bnb hello" in out


def test_core_helpers():
    from bitsandbricks.core import git_blob_sha, newest

    assert git_blob_sha("hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"  # git hash-object
    assert newest(["v1.2.0", "v1.10.0", "v1.9.9"]) == "v1.10.0"


def test_readme_names_every_command():
    from bitsandbricks.cli import ON_REPOS

    readme = (Path(__file__).parent.parent / "README.md").read_text()
    for cmd in (*ON_REPOS, "pr", "open", "init", "config", "plugins", "tui", "new", "login", "logout", "whoami",
                "market"):
        assert f"bnb {cmd}" in readme, cmd


def test_new_repo(env):
    out = bnb(env, "new", "--dry-run", "acme/plugin-c")
    assert "== acme/plugin-c (group plugins)" in out
    assert "[dry-run] create private acme/plugin-c from the template acme/plugin-tpl" in out
    assert "[dry-run] PATCH /repos/acme/plugin-c" in out and "[dry-run] POST /repos/acme/plugin-c/rulesets" in out
    out = bnb(env, "new", "--dry-run", "--public", "acme/site")
    assert "[dry-run] create public acme/site with a README" in out
    assert "[dry-run] PUT /repos/acme/site/contents/SECURITY.md" in out
    assert "exists already" in bnb(env, "new", "acme/web")
    assert "No group 'nope'" in bnb(env, "new", "acme/x", "--group", "nope")
