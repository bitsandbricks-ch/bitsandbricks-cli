# Mason

Mason (the `mason` command) is a terminal tool, TUI and web app for looking after many GitHub (and, in the web app, GitLab) repositories at once, across orgs and user accounts: status, pull requests, merges, workflow runs and approvals, security alerts, stats, and drift checks against shared defaults. It holds your bricks together.

The defaults live in a repository you choose. Point `mason` at any repo (or local folder) that holds a `mason.toml`, and it checks your repos against it. Nothing is tied to one org: every user can keep their own defaults repo.

## Install

Mason isn't on PyPI yet, so install it from GitHub with pipx. The `tui` extra adds `mason tui`, `server` adds `mason serve` (the web app), and `market` adds signature checks for `mason market`:

```sh
pipx install "mason-cli[tui,server] @ git+https://github.com/bitsandbricks-ch/mason-cli"
gh auth login          # on its own, mason talks to GitHub through the gh CLI and never sees a token
mason init your-org/your-defaults
```

To update: `pipx install --force "mason-cli[tui,server] @ git+https://github.com/bitsandbricks-ch/mason-cli"`. The install builds the web UI into the package, which needs Node.js (`npm`) on your PATH; without it, mason installs fine but `mason serve` has no UI and tells you how to build it. `mason serve --check` shows whether the UI is built. The Docker image always has it (see [Mason (web app)](#mason-web-app)).

The project was called Mortar before, and before that it was `bitsandbricks-ch/bitsandbricks-cli` with a `bnb` command. Only the names `mason`, `MASON_*`, `mason.toml` and `~/.config/mason` work now.

## Commands

Repos are named as `owner/name`, as a bare `name` when only one repo has it, or by their group alias (the group's `prefix` dropped, so `plugin-budgets` is also `budgets`). `name#12` is a pull request. Without names a command runs on every repo of your orgs. `--org` and `--group` narrow it down, and `--dry-run` shows what would change.

| Command | What it does |
|---|---|
| `mason init <owner/repo or folder>` | Use this defaults repo. A local folder without `mason.toml` gets a starter file. |
| `mason config` | Where mason looks: config file, defaults, orgs, groups, plugins. |
| `mason repos` | The repos mason sees, with their group. |
| `mason status` | One line per repo: group, latest release, PRs ready/draft, CI. One GraphQL query per 20 repos. |
| `mason prs` | Open PRs with checks, reviews, conflicts and auto-merge. |
| `mason pr web#12 [--diff] [--body]` | One PR in detail: merge state, failing checks with links, files, description (`--body`: all of it). Takes a PR URL too. |
| `mason merge [names] [--now] [--ready] [--method squash\|merge\|rebase]` | Auto-merge open PRs (or merge now; squash by default). Drafts and conflicts stay open; `--ready` marks named drafts ready first. |
| `mason runs [--failed] [--limit 3]` | Latest workflow runs per repo. |
| `mason rerun name [--run ID] [--yes]` | Re-runs the failed jobs of the repo's newest failed run (or of run ID), after listing its jobs and the step that failed. Jobs that passed and approvals already given stay. |
| `mason releases [--problems]` | Each repo's latest release, its assets, and its release runs. Flags assets the group's `release_assets` wants but the release lacks, and a failed last release run with the job and step that failed. |
| `mason packages [--problems]` | Container images on GHCR of your orgs: newest tags, whether the repo's latest release has an image with its tag, and a failed last image build (a run named or filed like image, docker or container) with the job and step that failed. |
| `mason bump name [patch\|minor\|major\|1.4.0] [--auto-merge]` | Opens a PR on `bump/v<new>` that sets the version in the group's `version` file (plugin.yaml, pyproject.toml, package.json, VERSION, ...) and adds the section to each `changelog`: Keep a Changelog `[Unreleased]` entries become `## [1.4.0] - date`, otherwise a `## 1.4.0` section lists the PRs merged since the last release. Default: patch. |
| `mason release new name v1.4.0 [--title T] [--notes TEXT\|-] [--draft] [--prerelease] [--target REF] [--generate-notes] [--yes]` | Creates a release by hand, after asking (GitHub creates the tag on the default branch or `--target` when it is new). `--generate-notes` lets GitHub write the notes from the merged PRs. `mason release view name [tag]` shows one release in full: notes, assets, the runs of its tag, and the repo's other recent releases. |
| `mason release [names] [--yes] [--list]` | Pushes the tag `v<version>` on the default branch of each named repo whose version is newer than its latest release (and whose first changelog has the section), after asking. The repo's own workflow builds and publishes. When the tag is there already it shows its release run. No names (or `--list`): a table of version against latest release. |
| `mason approve [--yes]` | Runs waiting for you: environments you review, and outside contributors' runs. A run that waits for someone else says why you can't approve it, for example "Prevent self-review" when you started it and are its only reviewer. |
| `mason security` | Open Dependabot, secret scanning and code scanning alerts. |
| `mason doctor [names] [--fix]` | Settings, security, release immutability, rulesets and files against the defaults. `--fix` applies them. |
| `mason sync [names] [--only PATH]` | Opens a PR per repo whose shared files differ. |
| `mason issues [names] [--mine] [--label L] [--assignee X] [--stale DAYS] [--state open\|closed\|all] [--limit 20]` | Issues across repos, newest activity first; PRs are left out. `--assignee none` shows unassigned ones, `*` any assigned; `--label` repeats. |
| `mason issue new name "title" [--body TEXT] [--label L] [--assignee X]` | Opens an issue (`--body -` reads it from stdin). `mason issue view\|close\|reopen name#12` shows, closes (`--not-planned`) or reopens one, `mason issue comment name#12 "text"` comments. An issue URL works too. |
| `mason workflows [names] [--fix]` | Risky GitHub Actions workflows: actions not pinned to a commit, `write-all` or missing `permissions`, `pull_request_target` that checks out the PR, script injection from `${{ github.event... }}`. `--fix` opens a PR per repo that pins the actions to commit SHAs. |
| `mason new owner/name [--group G] [--public] [--description TEXT]` | Creates a repo the way its group wants it: from the reference repo when that is a GitHub template, the group's files committed, then its settings, security and rulesets applied. Private unless `--public`. |
| `mason open web#12 [--actions] [--releases]` | Prints the link, and opens it (or copies it over SSH; `MASON_OPEN=browser\|copy\|print`). `--actions` and `--releases` link those pages of a repo. |
| `mason stats [--days 30]` | Pull requests merged and opened, median time to merge, open and stale PRs, workflow runs with success rate and duration, releases. |
| `mason plugins` | Installed plugins. |
| `mason market [install NAME] [--index URL]` | Plugins in the signed plugin index; `install` adds one (only from a verified index). `keygen`, `sign` and `verify` are for whoever publishes an index. |
| `mason serve [--host 127.0.0.1] [--port 8080]` | Runs Mason, the web app, on Flask's development server (needs the `server` extra). `mason serve --check` shows what its settings allow and what is missing; `mason serve --users` lists its accounts, and `mason operator add github:LOGIN` makes someone an operator (the operator page). See [docs/mason.md](docs/mason.md). |
| `mason login https://mason [--read] [--key KEY]` | Go through [Mason](docs/mason.md), the web app, with an API key: mason shows a code, you approve it in the browser, and every command and the TUI then work without gh or a GitHub token here. `--read` asks for a read-only key, `--key` uses one you made in Settings › API keys. `mason logout` forgets the key, `mason whoami` says how mason reaches GitHub and as whom. |
| `mason tui` | Everything above, full screen. |

Security alerts need the `security_events` scope and packages the `read:packages` scope: `gh auth refresh -s security_events,read:packages`.

## TUI

`mason tui` has tabs for Repos, Pull requests, Runs, Releases, Packages, Security, Checks (doctor plus plugin checks) and Settings (defaults repo, orgs, groups, plugins, theme, config file), plus any tabs that plugins add. Issues and workflow hardening are commands and web app tabs, not TUI tabs yet.

- **Keys per row:** the footer and the top of the right side list only the keys that do something for the selected row (approve only on a run that waits, re-run only on a failed one, ready only on a draft).
- **Pull requests:** **u** updates the branch from its base, **c** comments, **C** closes without merging, besides merge, ready and diff.
- **Runs:** the right side lists the selected run's jobs with the step that failed. **a** approves, **x** re-runs the failed jobs, **l** shows the end of each failed job's log, **C** cancels a running run.
- **Repos** show the version file against the latest release, CI on the default branch, alerts and missing release assets. **Releases** show the version, the release notes and what failed in the last release run.
- **Repos and Releases:** **b** bumps the selected repo's version (pick patch, minor or major; opens the PR), **R** pushes its release tag once the bump is merged, **N** creates a release by hand (tag, title, one line of notes or GitHub's own, published, pre-release or draft).
- **Enter** on a row opens it as a full page: what you can do with it on the left (Enter or the key in brackets runs it), its whole detail on the right, and what the actions printed at the bottom. On Releases the page lists the repo's recent releases with the newest one's whole notes. **Esc** goes back to the list. On Checks and Settings, Enter opens a short menu instead.
- **The footer** only shows the keys that work on the current tab.
- **n** creates a new repo.
- **ctrl+p** lists every command.
- **Loading:** nothing reloads by itself. The repo and PR list loads at start, with one GraphQL query per 20 repos. Runs, Releases, Packages and Security load the first time you open their tab. **r** reloads the tab you are on.
- **Rate limit:** the header shows how many API calls are left. Answers are cached for 60 seconds; **r** reads past the cache. If GitHub's rate limit is reached, the TUI says when it resets instead of failing.

## Configuration

Your own config is `~/.config/mason/config.toml` (`mason init` writes it):

```toml
defaults = "your-org/your-defaults"   # a GitHub repo, or a local folder
ref = "main"                          # optional branch or tag
orgs = ["another-org"]                # added to the orgs the defaults name
repos = ["someone/their-repo"]        # added to the defaults' repos
exclude = ["another-org/archive-*"]   # added to the defaults' exclude
```

It lives in `$XDG_CONFIG_HOME/mason` when that is set, and `MASON_CONFIG` points mason at another file. `MASON_DEFAULTS`, `MASON_DEFAULTS_REF` and `MASON_ORGS` (comma separated, replacing the orgs) override it for one run.

| Variable | What it does |
|---|---|
| `MASON_CONFIG` | Your config file, instead of `~/.config/mason/config.toml`. |
| `MASON_DEFAULTS`, `MASON_DEFAULTS_REF` | The defaults repo (or folder) and its branch or tag. |
| `MASON_ORGS` | The orgs to look at, comma separated. |
| `MASON_SERVER`, `MASON_KEY` | Go through Mason with this URL and API key (for CI). `MASON_SERVER=` (set but empty) uses gh even after `mason login`. |
| `MASON_OPEN` | How `mason open` opens links: `browser`, `copy` or `print`. |
| `MASON_PLUGINS` | `none`, or a comma separated list of the plugins to load. |
| `MASON_PLUGIN_INDEX`, `MASON_PLUGIN_INDEX_KEYS` | The plugin index for `mason market`, and extra public keys (base64, comma separated) it may be signed with. |

`mason.toml` in the defaults repo is shared by everyone who points mason at it:

```toml
orgs = ["your-org", "your-user"]
repos = ["someone/one-more-repo"]
exclude = ["your-org/old-*"]

[[group]]
name = "plugins"
match = { org = "your-org", prefix = "plugin-" }   # also: suffix, repos, exclude, topics, provider
from = "your-org/plugin-template"                  # a live reference repo
files = [".gitignore", "LICENSE"]                  # files that must match the reference
require = { allow_auto_merge = true }              # what the reference itself must have

[[group]]
name = "default"                                   # no match: takes the rest
settings = { delete_branch_on_merge = true, has_wiki = false }
security = { secret_scanning = "enabled", secret_scanning_push_protection = "enabled",
             dependabot_alerts = "enabled", dependabot_security_updates = "enabled" }
immutable_releases = true
vulnerability_reporting = true
topics = ["your-org"]                              # topics every repo has
labels = { bug = "d73a4a", docs = { color = "0075ca", description = "Documentation" } }
actions = { allowed_actions = "selected", default_workflow_permissions = "read",
            can_approve_pull_request_reviews = false }
required_files = ["README.md", "LICENSE", "CODEOWNERS|.github/CODEOWNERS"]   # A|B: either one
branch = "main"                                    # the default branch's name
visibility = "private"                             # public, private or internal
description = true                                 # every repo has a description
workflows = { allow_unpinned = ["actions/*"] }     # doctor checks the workflows too (or: workflows = true)
skip = ["labels"]                                  # checks this group leaves out
release_assets = ["*.zip"]                         # files every release must attach (mason releases)
version = "pyproject.toml"                         # the file with the version (mason bump, mason release)
changelog = ["CHANGELOG.md"]                       # changelogs mason bump adds the section to
rulesets = "rulesets/default"                      # ruleset JSON files (export them from GitHub)
files = "files/default"                            # copied into each repo, path for path
```

The first group that matches a repo is its group. With `from`, the group takes everything from the reference repo as it is live on GitHub (settings, security, release immutability, rulesets), so changing the reference changes what every repo of the group should have. Explicit `settings`, `security` and `rulesets` go on top.

A ruleset from the defaults folder only has to be covered: a repo may set more than the file says (GitHub fills in its own defaults). A reference repo's rulesets must match exactly. Each repo's own `.github/rulesets/*.json` must also match what's live, and `doctor --fix` imports them.

`doctor --fix` sets settings, security (Dependabot alerts and security updates too), topics (it adds the missing ones and keeps the rest), labels (it creates missing ones and fixes colors, and leaves other labels alone) and the Actions permissions. `branch`, `visibility`, `description` and `required_files` are only checked, because changing them can break things: a missing required file is a job for `mason sync` or a PR.

`skip` turns checks off for a group: `settings`, `security`, `vulnerability_reporting`, `immutable_releases`, `rulesets`, `own_rulesets` (the repo's `.github/rulesets`), `files`, `required_files`, `topics`, `labels`, `actions`, `branch`, `visibility`, `description`, `workflows` and `plugins` (the plugins' doctor checks).

`workflows` makes doctor check the group's workflow files like `mason workflows` does. It takes `true` or a table: `pinned`, `permissions`, `pull_request_target` and `injection` turn single checks off with `false`, and `allow_unpinned = ["actions/*", "your-org/*"]` lets actions of owners you trust stay on a tag. `mason workflows --fix` keeps the tag as a comment (`uses: actions/checkout@<sha> # v4`), so Dependabot and Renovate keep updating the pins.

Unknown keys on a group stay there for plugins (`group.extra`).

## Plugins

A plugin is a Python package with an entry point in the `mason.plugins` group. It can add commands, `status` columns (with extra GraphQL fields), doctor checks and hints, TUI tabs and palette commands. See `src/mason/plugins.py`.

```toml
[project.entry-points."mason.plugins"]
myplugin = "my_package.mason:MyPlugin"
```

Install one next to mason with `pipx inject mason-cli <package>`. `MASON_PLUGINS=none` (or a comma separated list of names) limits which plugins load.

hubra-cli is one: it adds Hubra plugin releases, signing checks and a Hubra tab.

## Mason (web app)

`mason serve` runs mason as a web app. People sign in with their own GitHub or GitLab account (OAuth, so every
call uses their own token) and get every command and TUI view in a terminal-style browser UI, plus issues,
workflow hardening, a defaults editor with templates, stats, a console, plugins and a signed plugin
marketplace. It works on a phone too. `mason login https://mason.mortar.ch` points the CLI and TUI at it with an
API key, so a laptop needs neither gh nor a token.

The quickest way to run it is Docker, which builds the UI too:

```sh
cp deploy/mason.env.example deploy/mason.env       # fill in MASON_URL, an OAuth app, who may sign in
docker compose -f deploy/docker-compose.yml up -d    # Mason and Postgres, on 127.0.0.1:8080
```

From a clone, for development:

```sh
docker compose -f deploy/docker-compose.yml up -d   # after filling in deploy/.env (see docs/mason.md)
pip install -e ".[server]" && (cd web && npm ci && npm run build)
mason serve --check && mason serve                       # http://localhost:8080
```

![Mason's pull requests view](docs/screenshots/03-pull-requests.png)

Setup, settings, the security model and more screenshots: [docs/mason.md](docs/mason.md).

## Roadmap

Ideas and planned features live in [docs/BACKLOG.md](docs/BACKLOG.md).

## Development

```sh
pip install -e ".[dev]"
pytest          # runs every command against tests/fake_gh, a stand-in for gh
ruff check .
cd web && npm ci && npm run build   # Mason's UI (npm run dev for a live-reloading one)
```

## License

MIT, see [LICENSE](LICENSE).
