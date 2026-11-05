# bitsandbricks

`bitsandbricks` (short: `bnb`) is a terminal tool and TUI for looking after many GitHub repositories at once, across orgs and user accounts: status, pull requests, merges, workflow runs and approvals, security alerts, and drift checks against shared defaults.

The defaults live in a repository you choose. Point `bnb` at any repo (or local folder) that holds a `bitsandbricks.toml`, and it checks your repos against it. Nothing is tied to one org: every user can keep their own defaults repo.

## Install

```sh
pipx install "git+https://github.com/bitsandbricks-ch/bitsandbricks-cli#egg=bitsandbricks-cli[tui]"
gh auth login          # bnb talks to GitHub through the gh CLI and never sees a token
bnb init your-org/your-defaults
```

To update a git install to a new version: `pipx install --force "git+https://..."`.

## Commands

Repos are named as `owner/name`, as a bare `name` when only one repo has it, or by their group alias (the group's `prefix` dropped, so `plugin-budgets` is also `budgets`). `name#12` is a pull request. Without names a command runs on every repo of your orgs. `--org` and `--group` narrow it down, and `--dry-run` shows what would change.

| Command | What it does |
|---|---|
| `bnb init <owner/repo or folder>` | Use this defaults repo. A local folder without `bitsandbricks.toml` gets a starter file. |
| `bnb config` | Where bnb looks: config file, defaults, orgs, groups, plugins. |
| `bnb repos` | The repos bnb sees, with their group. |
| `bnb status` | One line per repo: group, latest release, PRs ready/draft, CI. One GraphQL query per 20 repos. |
| `bnb prs` | Open PRs with checks, reviews, conflicts and auto-merge. |
| `bnb pr web#12 [--diff]` | One PR in detail: merge state, failing checks with links, files, description. |
| `bnb merge [names] [--now] [--ready]` | Auto-merge open PRs (or merge now). Drafts and conflicts stay open. |
| `bnb runs [--failed]` | Latest workflow runs. |
| `bnb approve [--yes]` | Runs waiting for you: environments you review, and outside contributors' runs. |
| `bnb security` | Open Dependabot, secret scanning and code scanning alerts. |
| `bnb doctor [names] [--fix]` | Settings, security, release immutability, rulesets and files against the defaults. `--fix` applies them. |
| `bnb sync [names] [--only PATH]` | Opens a PR per repo whose shared files differ. |
| `bnb open web#12` | Prints the link, and opens it (or copies it over SSH; `BNB_OPEN=browser\|copy\|print`). |
| `bnb plugins` | Installed plugins. |
| `bnb tui` | Everything above, full screen. |

Security alerts need the `security_events` scope: `gh auth refresh -s security_events`.

## Configuration

Your own config is `~/.config/bitsandbricks/config.toml` (`bnb init` writes it):

```toml
defaults = "your-org/your-defaults"   # a GitHub repo, or a local folder
ref = "main"                          # optional branch or tag
orgs = ["another-org"]                # added to the orgs the defaults name
```

`BNB_DEFAULTS`, `BNB_DEFAULTS_REF` and `BNB_ORGS` (comma separated) override it for one run.

`bitsandbricks.toml` in the defaults repo is shared by everyone who points bnb at it:

```toml
orgs = ["your-org", "your-user"]
repos = ["someone/one-more-repo"]
exclude = ["your-org/old-*"]

[[group]]
name = "plugins"
match = { org = "your-org", prefix = "plugin-" }   # also: suffix, repos, exclude, topics
from = "your-org/plugin-template"                  # a live reference repo
files = [".gitignore", "LICENSE"]                  # files that must match the reference
require = { allow_auto_merge = true }              # what the reference itself must have

[[group]]
name = "default"                                   # no match: takes the rest
settings = { delete_branch_on_merge = true, has_wiki = false }
security = { secret_scanning = "enabled", secret_scanning_push_protection = "enabled" }
immutable_releases = true
vulnerability_reporting = true
rulesets = "rulesets/default"                      # ruleset JSON files (export them from GitHub)
files = "files/default"                            # copied into each repo, path for path
```

The first group that matches a repo is its group. With `from`, the group takes everything from the reference repo as it is live on GitHub (settings, security, release immutability, rulesets), so changing the reference changes what every repo of the group should have. Explicit `settings`, `security` and `rulesets` go on top.

A ruleset from the defaults folder only has to be covered: a repo may set more than the file says (GitHub fills in its own defaults). A reference repo's rulesets must match exactly. Each repo's own `.github/rulesets/*.json` must also match what's live, and `doctor --fix` imports them.

Unknown keys on a group stay there for plugins (`group.extra`).

## Plugins

A plugin is a Python package with an entry point in the `bitsandbricks.plugins` group. It can add commands, `status` columns (with extra GraphQL fields), doctor checks and hints, TUI tabs and palette commands. See `src/bitsandbricks/plugins.py`.

```toml
[project.entry-points."bitsandbricks.plugins"]
hubra = "hubra_cli.bnb:HubraPlugin"
```

Install one next to bnb with `pipx inject bitsandbricks-cli <package>`.

## Roadmap

- GitLab backend next to GitHub.
- Kubernetes (k3s) and Docker views as plugins.
- A plugin index to find and install plugins.
- An AI agent that proposes fixes for security alerts and merge conflicts as PRs.

## Development

```sh
pip install -e ".[dev]"
pytest          # runs every command against tests/fake_gh, a stand-in for gh
ruff check .
```
