# Mortar

Mortar is mtr as a web app: it holds the bricks together. People sign in with their own GitHub and GitLab accounts and get every
CLI and TUI view in a terminal-style browser UI: repos, pull requests, runs, approvals, releases, packages,
issues, security alerts, doctor/fix/sync, workflow hardening, stats, a defaults editor, a `mtr` console,
plugins and a plugin marketplace.
The same API serves `mtr` and `mtr tui` on a laptop: `mtr login https://mortar` and they need neither gh
nor a GitHub token.

```
browser (Vue, web/)  ─┐                         ┌─> GitHub REST/GraphQL  (the user's own OAuth token)
mtr / mtr tui (key) ──┼─> /api/v1  (Flask)  ────┤
scripts (key)        ─┘      │                  └─> GitLab REST/GraphQL  (the user's own OAuth token)
                             └─> database: users, logins (tokens encrypted), sessions, API keys, audit
```

## Screenshots

![login](screenshots/01-login.png)
![repos](screenshots/02-repos.png)
![pull requests](screenshots/03-pull-requests.png)
![runs](screenshots/04-runs.png)
![stats](screenshots/05-stats.png)
![checks](screenshots/06-checks.png)
![defaults editor](screenshots/07-defaults-editor.png)
![console](screenshots/08-console.png)
![plugins](screenshots/09-plugins.png)
![market](screenshots/10-market.png)
![settings](screenshots/11-settings.png)
![audit](screenshots/12-audit.png)
![theme amber](screenshots/13-theme-amber.png)
![theme paper](screenshots/14-theme-paper.png)

On a phone (the layout adapts below 700px; it can be added to the home screen):

<img src="screenshots/phone/m01-login.png" alt="login" width="200"> <img src="screenshots/phone/m02-repos.png" alt="repos" width="200"> <img src="screenshots/phone/m03-prs.png" alt="prs" width="200"> <img src="screenshots/phone/m05-approvals.png" alt="approvals" width="200"> <img src="screenshots/phone/m06-stats.png" alt="stats" width="200"> <img src="screenshots/phone/m08-defaults.png" alt="defaults" width="200"> <img src="screenshots/phone/m12-settings.png" alt="settings" width="200"> <img src="screenshots/phone/m14-console-run.png" alt="console run" width="200">

## The web UI

The tabs are repos, pull requests, issues, runs, approvals, releases, packages, security, checks, workflows,
stats, defaults, console, plugins, market, settings and audit, plus tabs that plugins add. Each has a
one-key shortcut (`1` to `9` for the first ones, `I` issues, `W` workflows, `D` defaults, `:` console,
`P` plugins, `K` market, `S` settings, `L` audit).

- **Issues** lists issues across GitHub repos and GitLab projects, filtered by state, label, mine and age.
  It closes, reopens and comments on them, and **n** opens a new issue in any repo.
- **Workflows** shows what `mtr workflows` finds; **p** opens a PR that pins the selected repo's actions
  to commit SHAs.
- **Settings** holds the defaults repo, orgs, extra and left-out repos, GitLab groups and projects,
  connected accounts, API keys, sessions and plugin secrets.
- **Keys:** arrows or `j`/`k` move, Enter acts, `/` filters, `r` reloads, `t` switches the theme
  (phosphor, amber, paper), `?` lists the keys.

## Run it

Mortar needs the `server` extra and the built UI. A pipx install from git
(`pipx install "mortar-cli[tui,server] @ git+https://github.com/bitsandbricks-ch/mortar-cli"`) has
`mtr serve`, but not the UI's built files, so run it from the Docker image or a clone:

```sh
pip install -e ".[server]"                   # flask, sqlalchemy, cryptography, gunicorn
(cd web && npm ci && npm run build)          # the UI, into src/mortar/server/static
export GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=...
export MTR_ADMINS=github:you MTR_DEV=1
mtr serve --check                            # what the settings allow, and what is missing
mtr serve                                    # http://localhost:8080 (Flask's development server)
mtr serve --host 0.0.0.0 --port 9000         # somewhere else
```

UI development: `cd web && npm run dev` serves the UI on :5173 and passes `/api` to Mortar on :8080.

In production use the image: `docker build -t mortar .`, or `deploy/docker-compose.yml`
(Mortar + Postgres on `127.0.0.1:8080`; copy `deploy/mortar.env.example` to `deploy/mortar.env`). Put it behind a TLS
proxy and set `MTR_TRUSTED_PROXIES=1` so it sees the real client address. The image runs
gunicorn with one process and many threads, which keeps each user's API cache in one place.

## Signing in

People sign in in the browser with GitHub or GitLab through an OAuth app you register (below): the
authorization code flow with PKCE. There is no GitHub or GitLab device flow for the browser sign-in. The
device-code flow Mortar has is its own, for `mtr login` (see [The CLI and TUI through
Mortar](#the-cli-and-tui-through-mortar)). Someone signed in with one provider connects the other in
Settings › Accounts, and both logins then belong to the same Mortar user.

Who may sign in is up to you: `MTR_ALLOW_USERS`, `MTR_ALLOW_ORGS` and `MTR_ADMINS`, or anyone with
`MTR_ALLOW_ANYONE=1`. Mortar doesn't start until one of them is set.

## OAuth apps

| provider | where | callback URL | scopes |
| --- | --- | --- | --- |
| GitHub | Settings › Developer settings › OAuth Apps | `{MTR_URL}/api/v1/auth/github/callback` | `repo read:org workflow security_events read:packages` |
| GitLab | Preferences (or Admin) › Applications, confidential | `{MTR_URL}/api/v1/auth/gitlab/callback` | `api` |

GitHub Enterprise: set `GITHUB_URL` (and `GITHUB_API_URL` if it isn't `{GITHUB_URL}/api/v3`). Self-managed
GitLab: set `GITLAB_URL`.

## Settings (environment)

| variable | meaning |
| --- | --- |
| `MTR_URL` | where people reach it; must be `https://` unless `MTR_DEV=1` |
| `MTR_SECRET_KEY` | encrypts stored tokens and secrets; made once in the data folder when unset |
| `MTR_DATA` | data folder (default `./data`, `/data` in the image) |
| `DATABASE_URL` | SQLAlchemy URL; SQLite in the data folder by default, `postgresql+psycopg://…` with `[postgres]` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_URL`, `GITHUB_API_URL`, `GITHUB_SCOPES` | the GitHub login (scopes default to the table above) |
| `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_URL`, `GITLAB_SCOPES` | the GitLab login |
| `MTR_ALLOW_USERS` | who may sign in: `github:mike, gitlab:ann`, or a bare login for either |
| `MTR_ALLOW_ORGS` | members of these GitHub orgs or GitLab groups may sign in |
| `MTR_ADMINS` | admins (may sign in too): everyone's audit log, plugins for everyone, installs |
| `MTR_ALLOW_ANYONE` | `1` lets any account sign in (on purpose only) |
| `MTR_IDLE_HOURS`, `MTR_SESSION_DAYS` | session idle timeout (12) and lifetime (7) |
| `MTR_CACHE` | how long GET answers are cached per user (`60s`); ETags make refreshes cheap |
| `MTR_PLUGIN_INDEX`, `MTR_PLUGIN_INDEX_KEYS` | the plugin index (URL or file; default: `plugins/index.json` on this repo's main branch) and extra public keys (base64, comma separated) it may be signed with |
| `MTR_PLUGIN_INSTALL` | `1` lets admins install plugins from the marketplace page |
| `MTR_TRUSTED_PROXIES` | how many proxies in front set `X-Forwarded-*` (0) |
| `MTR_DEV` | `1`: http allowed and `MTR_URL` defaults to `http://localhost:8080` (local development only); over http the session cookie loses its `__Host-` prefix |

Mortar refuses to start when no login is configured, nobody may sign in, or the URL isn't https.

## Security model

- **Sign-in**: OAuth authorization code with PKCE (S256) and a `state` bound to the browser by a short-lived
  cookie. Only allowed accounts get in; the decision is made at every sign-in, not once.
- **The user's own token, always.** Every GitHub and GitLab call is made with the signed-in user's token, so
  nobody sees or changes more than their account may. The server never falls back to a token or gh login of
  its own. Tokens are encrypted at rest (Fernet), refreshed when they expire (GitLab), and revoked at the
  provider when someone disconnects a login or deletes their account.
- **Sessions** are random cookies (`__Host-` prefixed, `Secure`, `HttpOnly`, `SameSite=Lax`); the database
  keeps only their SHA-256. Idle and absolute timeouts; users see and end their other sessions.
- **CSRF**: every change needs the session's `X-CSRF-Token` header and a matching `Origin`.
- **API keys** (`mtr_<id>_<secret>`) are stored hashed, have a scope (`read` or `write`), an optional expiry,
  and show their last use. Read keys can still ask for dry runs. Creating keys, deleting the account and
  installing plugins need a browser session, not a key.
- **Device login** (`mtr login URL`): the terminal shows a code, the user approves it in the browser and picks
  the scope; the terminal gets a key. Codes expire after 10 minutes and polling is rate limited.
- **The GitHub proxy** for signed-in CLIs passes on only the reads mtr makes and the writes mtr and its plugins
  make; deleting repos, changing access, keys, hooks and account settings are never passed on.
- **Audit log**: sign-ins, keys, every change (with its output) and every dry run, with who, how and from where.
- **Browser hardening**: a strict CSP (`self` only, no inline scripts or styles, no `data:` fonts), no
  `v-html` anywhere in the UI, `X-Frame-Options: DENY`, `no-referrer`, HSTS, `Cache-Control: no-store` on the API.
- **Throttling** on sign-in, device codes and key use.

## The CLI and TUI through Mortar

```sh
mtr login https://mortar.example.com        # device login; --read for a read-only key, --key KEY to paste one
mtr whoami
mtr status                                # every command now goes through Mortar
mtr tui
mtr logout                                # forgets the key here (revoke it in Settings › API keys)
```

`mtr login` asks Mortar for a code and opens the approval page in the browser (over SSH it prints the
link), where you pick read only or read and change; the terminal then gets the key. The key is kept in `~/.config/mortar/operator.json` (0600). `MTR_SERVER` and `MTR_KEY`
do the same for CI; `MTR_SERVER=` (set but empty) goes back to gh without logging out. The defaults repo
and orgs then come from your Mortar settings.

## Defaults

The defaults repo is the same `mortar.toml` the CLI reads (see the README). The defaults editor shows
its files, validates them as you type, offers templates (groups, rulesets, shared files), copies a live
repo's rulesets into the defaults, and saves through a pull request (or merge request) by default, so changes
to the defaults are reviewed like code. The defaults repo itself can live on GitHub or GitLab.

Groups can now match GitLab projects and carry GitLab settings:

```toml
[[group]]
name = "gitlab-services"
match = { provider = "gitlab", prefix = "svc-" }
files = "files/gitlab"

[group.gitlab]
settings = { only_allow_merge_if_pipeline_succeeds = true, remove_source_branch_after_merge = true }
protected_branches = { main = { push_access_level = 0, merge_access_level = 40 } }  # 0 no one, 30 developers, 40 maintainers
approvals_before_merge = 1
```

GitLab projects are named `gitlab:group/sub/project`. List them in Settings (`GitLab groups`, `GitLab projects`).

## Plugins and the marketplace

Plugins are the same Python entry points the CLI loads. In Mortar they can also add:

- `web_actions()`: buttons with parameters (`write: True` actions ask first, are audited, and honor dry run),
- `web_commands`: commands the console allows,
- `web_secrets()`: per-user secrets (e.g. a Hubra token) that users set in Settings and plugins read with
  `plugins.secret(name)` while serving that user.

Each user switches plugins on or off for themselves; admins can switch one off for everyone.

The marketplace reads a plugin index (this repo's `plugins/index.json` by default) signed with Ed25519 (`mtr market
keygen`, `mtr market sign`). Unsigned or wrongly signed indexes are shown but nothing installs from them.
Installing from the web is off unless `MTR_PLUGIN_INSTALL=1`, and only admins can do it; restart
Mortar after an install. The signing code is adapted from labdex (relicensed into Mortar under MIT by its author).

## API

Everything the UI does is JSON under `/api/v1` and works with an API key (`Authorization: Bearer mtr_…`):

`/me`, `/config`, `/repos`, `/repo`, `/status`, `/prs`, `/pr`, `/merge`, `/ready`, `/issues`, `/issue`,
`/runs`, `/approvals`, `/approve`, `/releases`, `/releases/jobs`, `/packages`, `/security`, `/doctor`, `/sync`,
`/workflows`, `/workflows/pin`, `/new`, `/metrics?days=`, `/console`, `/defaults…`, `/plugins…`, `/market…`,
`/settings`, `/keys`, `/sessions`, `/secrets`, `/audit`, `/rate`, `/cache/clear`, and for admins
`/admin/users` and `/admin/stats`. `/api/v1/health` needs no sign-in. Changes take `"dry_run": true`.
