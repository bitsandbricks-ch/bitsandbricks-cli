# Mortar

Mortar (`mortar`, short: `bnb`) is a terminal tool, TUI and web app for looking after many GitHub (and, in the web app, GitLab) repositories at once, across orgs and user accounts: status, pull requests, merges, workflow runs and approvals, security alerts, stats, and drift checks against shared defaults. It holds your bricks together.

Formerly bitsandbricks: the `bnb` and `bitsandbricks` commands, `import bitsandbricks`, plugins registered under `bitsandbricks.plugins`, `~/.config/bitsandbricks` and the `BNB_OPERATOR_*` settings all keep working.

The defaults live in a repository you choose. Point `bnb` at any repo (or local folder) that holds a `bitsandbricks.toml`, and it checks your repos against it. Nothing is tied to one org: every user can keep their own defaults repo.

## Install

```sh
pipx install "mortar-cli[tui] @ git+https://github.com/bitsandbricks-ch/bitsandbricks-cli"
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
| `bnb rerun name [--run ID] [--yes]` | Re-runs the failed jobs of the repo's newest failed run (or of run ID), after listing its jobs and the step that failed. Jobs that passed and approvals already given stay. |
| `bnb releases [--problems]` | Each repo's latest release, its assets, and its release runs. Flags assets the group's `release_assets` wants but the release lacks, and a failed last release run with the job and step that failed. |
| `bnb packages [--problems]` | Container images on GHCR of your orgs: newest tags, whether the repo's latest release has an image with its tag, and a failed last image build (a run named or filed like image, docker or container) with the job and step that failed. |
| `bnb bump name [patch\|minor\|major\|1.4.0] [--auto-merge]` | Opens a PR on `bump/v<new>` that sets the version in the group's `version` file (plugin.yaml, pyproject.toml, package.json, VERSION, ...) and adds the section to each `changelog`: Keep a Changelog `[Unreleased]` entries become `## [1.4.0] - date`, otherwise a `## 1.4.0` section lists the PRs merged since the last release. Default: patch. |
| `bnb release [names] [--yes]` | Pushes the tag `v<version>` on the default branch of each named repo whose version is newer than its latest release (and whose first changelog has the section), after asking. The repo's own workflow builds and publishes. When the tag is there already it shows its release run. No names: a table of version against latest release. |
| `bnb approve [--yes]` | Runs waiting for you: environments you review, and outside contributors' runs. A run that waits for someone else says why you can't approve it, for example "Prevent self-review" when you started it and are its only reviewer. |
| `bnb security` | Open Dependabot, secret scanning and code scanning alerts. |
| `bnb doctor [names] [--fix]` | Settings, security, release immutability, rulesets and files against the defaults. `--fix` applies them. |
| `bnb sync [names] [--only PATH]` | Opens a PR per repo whose shared files differ. |
| `bnb new owner/name [--group G] [--public]` | Creates a repo the way its group wants it: from the reference repo when that is a GitHub template, the group's files committed, then its settings, security and rulesets applied. Private unless `--public`. |
| `bnb open web#12` | Prints the link, and opens it (or copies it over SSH; `BNB_OPEN=browser\|copy\|print`). |
| `bnb stats [--days 30]` | Pull requests merged and opened, median time to merge, open and stale PRs, workflow runs with success rate and duration, releases. |
| `bnb plugins` | Installed plugins. |
| `bnb market [install NAME]` | Plugins in the signed plugin index; `install` adds one (only from a verified index). `keygen`, `sign` and `verify` are for whoever publishes an index. |
| `mortar serve [--check]` | Run Mortar, the web app (`pipx inject mortar-cli 'mortar-cli[server]'`); `--check` shows what its settings allow. See [docs/mortar.md](docs/mortar.md). |
| `bnb login https://mortar` | Go through [Mortar](docs/mortar.md), the web app, with an API key: approve a code in the browser, and every command and the TUI then work without gh or a GitHub token here. `bnb logout` forgets the key, `bnb whoami` says how bnb reaches GitHub. |
| `bnb tui` | Everything above, full screen. |

Security alerts need the `security_events` scope and packages the `read:packages` scope: `gh auth refresh -s security_events,read:packages`.

## TUI

`bnb tui` has tabs for Repos, Pull requests, Runs, Releases, Packages, Security, Checks (doctor plus plugin checks) and Settings (defaults repo, orgs, groups, plugins, theme, config file), plus any tabs that plugins add.

- **Keys per row:** the footer and the top of the right side list only the keys that do something for the selected row (approve only on a run that waits, re-run only on a failed one, ready only on a draft).
- **Pull requests:** **u** updates the branch from its base, **c** comments, **C** closes without merging, besides merge, ready and diff.
- **Runs:** the right side lists the selected run's jobs with the step that failed. **a** approves, **x** re-runs the failed jobs, **l** shows the end of each failed job's log, **C** cancels a running run.
- **Repos** show the version file against the latest release, CI on the default branch, alerts and missing release assets. **Releases** show the version, the release notes and what failed in the last release run.
- **Repos and Releases:** **b** bumps the selected repo's version (pick patch, minor or major; opens the PR), **R** pushes its release tag once the bump is merged.
- **Enter** on a row opens it as a full page: what you can do with it on the left (Enter or the key in brackets runs it), its whole detail on the right, and what the actions printed at the bottom. **Esc** goes back to the list. On Checks and Settings, Enter opens a short menu instead.
- **The footer** only shows the keys that work on the current tab.
- **n** creates a new repo.
- **ctrl+p** lists every command.
- **Loading:** nothing reloads by itself. The repo and PR list loads at start, with one GraphQL query per 20 repos. Runs, Releases, Packages and Security load the first time you open their tab. **r** reloads the tab you are on.
- **Rate limit:** the header shows how many API calls are left. Answers are cached for 60 seconds. If GitHub's rate limit is reached, the TUI says when it resets instead of failing.

## Configuration

Your own config is `~/.config/mortar/config.toml` (or `~/.config/bitsandbricks/` from before the rename) (`bnb init` writes it):

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
release_assets = ["*.zip"]                         # files every release must attach (bnb releases)
version = "pyproject.toml"                         # the file with the version (bnb bump, bnb release)
changelog = ["CHANGELOG.md"]                       # changelogs bnb bump adds the section to
rulesets = "rulesets/default"                      # ruleset JSON files (export them from GitHub)
files = "files/default"                            # copied into each repo, path for path
```

The first group that matches a repo is its group. With `from`, the group takes everything from the reference repo as it is live on GitHub (settings, security, release immutability, rulesets), so changing the reference changes what every repo of the group should have. Explicit `settings`, `security` and `rulesets` go on top.

A ruleset from the defaults folder only has to be covered: a repo may set more than the file says (GitHub fills in its own defaults). A reference repo's rulesets must match exactly. Each repo's own `.github/rulesets/*.json` must also match what's live, and `doctor --fix` imports them.

Unknown keys on a group stay there for plugins (`group.extra`).

## Plugins

A plugin is a Python package with an entry point in the `mortar.plugins` group (`bitsandbricks.plugins` still works). It can add commands, `status` columns (with extra GraphQL fields), doctor checks and hints, TUI tabs and palette commands. See `src/mortar/plugins.py`.

```toml
[project.entry-points."mortar.plugins"]
hubra = "hubra_cli.bnb:HubraPlugin"
```

Install one next to bnb with `pipx inject mortar-cli <package>`. `BNB_PLUGINS=none` (or a comma separated list of names) limits which plugins load.

hubra-cli is one: it adds Hubra plugin releases, signing checks and a Hubra tab.

## Mortar (web app)

`mortar serve` runs bnb as a web app: people sign in with GitHub and GitLab (OAuth, their own tokens), and
get every command and TUI view in a terminal-style browser UI, plus a defaults editor with templates,
stats, a console, plugins and a signed plugin marketplace. `bnb login https://your-mortar` points the CLI
and TUI at it with an API key, so a laptop needs neither gh nor a token.

```sh
docker compose -f deploy/docker-compose.yml up -d   # after filling in deploy/mortar.env
```

![Mortar's pull requests view](docs/screenshots/03-pull-requests.png)

Setup, settings, the security model and more screenshots: [docs/mortar.md](docs/mortar.md).

## Roadmap

Ideas and planned features live in [docs/BACKLOG.md](docs/BACKLOG.md).

## Development

```sh
pip install -e ".[dev]"
pytest          # runs every command against tests/fake_gh, a stand-in for gh
ruff check .
cd web && npm ci && npm run build   # Mortar's UI (npm run dev for a live-reloading one)
```

## License

MIT, see [LICENSE](LICENSE).
