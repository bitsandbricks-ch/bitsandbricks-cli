"""Ready-made building blocks for a defaults repo: group snippets, rulesets and shared files.

The operator's defaults editor offers them; each lands as a file in the defaults repo (through a
PR by default), where `bnb doctor` and `bnb sync` pick it up like anything written by hand.
"""

from __future__ import annotations

import json

GROUPS = [
    {"id": "group-default", "title": "Group: everything else",
     "help": "Settings, security and files for every repo no other group takes",
     "text": """
[[group]]
name = "default"
settings = { delete_branch_on_merge = true, allow_auto_merge = true, allow_merge_commit = false, has_wiki = false }
security = { secret_scanning = "enabled", secret_scanning_push_protection = "enabled", dependabot_security_updates = "enabled" }
vulnerability_reporting = true
immutable_releases = true
rulesets = "rulesets/default"
files = "files/default"
"""},
    {"id": "group-template", "title": "Group: repos made from a template repo",
     "help": "The template repo's live settings and rulesets are the reference; listed files must match it",
     "text": """
[[group]]
name = "services"
match = { org = "your-org", prefix = "svc-" }
from = "your-org/svc-template"
files = [".gitignore", "LICENSE", ".github/pull_request_template.md"]
require = { allow_auto_merge = true }
release_assets = ["*.tar.gz"]
"""},
    {"id": "group-hardened", "title": "Group: hardened public repos",
     "help": "Security on, read-only workflow tokens, pinned actions, labels, topics and the files every repo needs",
     "text": """
[[group]]
name = "public"
match = { org = "your-org", topics = ["public"] }
settings = { delete_branch_on_merge = true, allow_merge_commit = false, has_wiki = false, web_commit_signoff_required = true }
security = { secret_scanning = "enabled", secret_scanning_push_protection = "enabled", dependabot_alerts = "enabled", dependabot_security_updates = "enabled" }
vulnerability_reporting = true
immutable_releases = true
topics = ["your-org"]
labels = { bug = "d73a4a", enhancement = "a2eeef", dependencies = "0366d6", security = "b60205" }
actions = { allowed_actions = "selected", default_workflow_permissions = "read", can_approve_pull_request_reviews = false }
required_files = ["README.md", "LICENSE", "SECURITY.md", "CODEOWNERS|.github/CODEOWNERS"]
branch = "main"
visibility = "public"
description = true
"""},
    {"id": "group-gitlab", "title": "Group: GitLab projects",
     "help": "Settings and protected branches for GitLab projects (provider = gitlab)",
     "text": """
[[group]]
name = "gitlab"
match = { provider = "gitlab" }
files = "files/gitlab"

[group.gitlab]
settings = { only_allow_merge_if_pipeline_succeeds = true, remove_source_branch_after_merge = true, squash_option = "default_on" }
protected_branches = { main = { push_access_level = 0, merge_access_level = 30 } }
approvals_before_merge = 1
"""},
]


def _rs(name: str, target: str, include: list[str], rules: list[dict]) -> str:
    return json.dumps({"name": name, "target": target, "enforcement": "active",
                       "conditions": {"ref_name": {"include": include, "exclude": []}}, "rules": rules,
                       "bypass_actors": []}, indent=2) + "\n"


RULESETS = [
    {"id": "rs-main", "title": "Ruleset: protect the default branch", "path": "rulesets/default/main.json",
     "help": "PRs only, no force pushes or deletion, linear history",
     "text": _rs("main", "branch", ["~DEFAULT_BRANCH"], [
         {"type": "deletion"}, {"type": "non_fast_forward"}, {"type": "required_linear_history"},
         {"type": "pull_request", "parameters": {"required_approving_review_count": 0, "dismiss_stale_reviews_on_push":
                                                 True, "require_code_owner_review": False,
                                                 "require_last_push_approval": False,
                                                 "required_review_thread_resolution": True}}])},
    {"id": "rs-checks", "title": "Ruleset: CI must pass", "path": "rulesets/default/checks.json",
     "help": "The `test` check must pass before merging (change the context to your job's name)",
     "text": _rs("checks", "branch", ["~DEFAULT_BRANCH"], [
         {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": False,
                                                           "required_status_checks": [{"context": "test"}]}}])},
    {"id": "rs-tags", "title": "Ruleset: release tags can't move", "path": "rulesets/default/release-tags.json",
     "help": "Tags v* can't be deleted or moved",
     "text": _rs("release tags", "tag", ["refs/tags/v*"], [{"type": "deletion"}, {"type": "update"}])},
    {"id": "rs-signed", "title": "Ruleset: signed commits", "path": "rulesets/default/signed.json",
     "help": "Every commit on the default branch must be signed",
     "text": _rs("signed commits", "branch", ["~DEFAULT_BRANCH"], [{"type": "required_signatures"}])},
]

FILES = [
    {"id": "f-pr", "title": "Pull request template", "path": "files/default/.github/pull_request_template.md",
     "text": "## What and why\n\n\n## How it was tested\n\n\n## Checklist\n\n- [ ] Tests\n- [ ] Docs\n"},
    {"id": "f-security", "title": "SECURITY.md", "path": "files/default/SECURITY.md",
     "text": "# Security\n\nPlease report vulnerabilities privately: use **Report a vulnerability** under this "
             "repository's Security tab. Don't open a public issue.\n"},
    {"id": "f-codeowners", "title": "CODEOWNERS", "path": "files/default/.github/CODEOWNERS",
     "text": "# Who reviews what. https://docs.github.com/articles/about-code-owners\n* @your-org/maintainers\n"},
    {"id": "f-dependabot", "title": "Dependabot (Actions and pip, weekly)", "path": "files/default/.github/dependabot.yml",
     "text": "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n    schedule: { interval: "
             "weekly }\n    groups: { actions: { patterns: ['*'] } }\n  - package-ecosystem: pip\n    directory: /\n"
             "    schedule: { interval: weekly }\n"},
    {"id": "f-bug", "title": "Issue form: bug report", "path": "files/default/.github/ISSUE_TEMPLATE/bug.yml",
     "text": "name: Bug\ndescription: Something doesn't work\nlabels: [bug]\nbody:\n  - type: textarea\n    attributes:"
             " { label: What happened, and what did you expect? }\n    validations: { required: true }\n  - type: "
             "textarea\n    attributes: { label: How to reproduce }\n  - type: input\n    attributes: { label: Version }\n"},
    {"id": "f-feature", "title": "Issue form: feature request",
     "path": "files/default/.github/ISSUE_TEMPLATE/feature.yml",
     "text": "name: Feature\ndescription: An idea for something new\nlabels: [enhancement]\nbody:\n  - type: textarea\n"
             "    attributes: { label: What would you like to do? }\n    validations: { required: true }\n"},
    {"id": "f-ci", "title": "Workflow: CI (Python)", "path": "files/default/.github/workflows/ci.yml",
     "text": "name: CI\non:\n  pull_request:\n  push:\n    branches: [main]\npermissions:\n  contents: read\njobs:\n"
             "  test:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout@v4\n      - uses: "
             "actions/setup-python@v5\n        with: { python-version: '3.13' }\n      - run: pip install -e '.[dev]'"
             "\n      - run: pytest -q\n"},
    {"id": "f-editorconfig", "title": ".editorconfig", "path": "files/default/.editorconfig",
     "text": "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\ncharset = utf-8\nindent_style = space\n"
             "indent_size = 4\n\n[*.{yml,yaml,json,md}]\nindent_size = 2\n"},
    {"id": "f-gitlab-mr", "title": "GitLab merge request template",
     "path": "files/gitlab/.gitlab/merge_request_templates/Default.md",
     "text": "## What and why\n\n\n## How it was tested\n\n/assign me\n"},
    {"id": "f-gitlab-ci", "title": "GitLab CI (Python)", "path": "files/gitlab/.gitlab-ci.yml",
     "text": "test:\n  image: python:3.13\n  script:\n    - pip install -e '.[dev]'\n    - pytest -q\n  rules:\n"
             "    - if: $CI_PIPELINE_SOURCE == 'merge_request_event'\n    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH\n"},
]


def catalog() -> list[dict]:
    out = [dict(t, kind="group", path="mortar.toml") for t in GROUPS]
    out += [dict(t, kind="ruleset") for t in RULESETS]
    out += [dict(t, kind="file", help=t.get("help", "")) for t in FILES]
    return out
