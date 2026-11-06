# Mortar

Mortar is bnb as a web app: it holds the bricks together. People sign in with their own GitHub and GitLab accounts and get every
CLI and TUI view in a terminal-style browser UI: repos, pull requests, runs, approvals, releases, packages,
security alerts, doctor/fix/sync, stats, a defaults editor, a `bnb` console, plugins and a plugin marketplace.
The same API serves `bnb` and `bnb tui` on a laptop: `bnb login https://mortar` and they need neither gh
nor a GitHub token.

```
browser (Vue, web/)  ─┐                         ┌─> GitHub REST/GraphQL  (the user's own OAuth token)
bnb / bnb tui (key) ──┼─> /api/v1  (Flask)  ────┤
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

## Run it

```sh
pip install -e ".[server]"                   # flask, sqlalchemy, cryptography, gunicorn
(cd web && npm ci && npm run build)          # the UI, into src/mortar/server/static
export GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=...
export MORTAR_ADMINS=github:you MORTAR_DEV=1
mortar serve --check                    # what the settings allow, and what is missing
mortar serve                           # http://localhost:8080 (development server)
```

UI development: `cd web && npm run dev` serves the UI on :5173 and passes `/api` to Mortar on :8080.

In production use the image: `docker build -t mortar .`, or `deploy/docker-compose.yml`
(Mortar + Postgres; copy `deploy/mortar.env.example` to `deploy/mortar.env`). Put it behind a TLS
proxy and set `MORTAR_TRUSTED_PROXIES=1` so it sees the real client address. The image runs
gunicorn with one process and many threads, which keeps each user's API cache in one place.

## OAuth apps

| provider | where | callback URL | scopes |
| --- | --- | --- | --- |
| GitHub | Settings › Developer settings › OAuth Apps | `{MORTAR_URL}/api/v1/auth/github/callback` | `repo read:org workflow security_events read:packages` |
| GitLab | Preferences (or Admin) › Applications, confidential | `{MORTAR_URL}/api/v1/auth/gitlab/callback` | `api` |

GitHub Enterprise: set `GITHUB_URL` (and `GITHUB_API_URL` if it isn't `{GITHUB_URL}/api/v3`). Self-managed
GitLab: set `GITLAB_URL`. Someone signed in with one provider connects the other in Settings › Accounts.

## Settings (environment)

| variable | meaning |
| --- | --- |
| `MORTAR_URL` | where people reach it; must be `https://` unless `MORTAR_DEV=1` |
| `MORTAR_SECRET_KEY` | encrypts stored tokens and secrets; made once in the data folder when unset |
| `MORTAR_DATA` | data folder (default `./data`, `/data` in the image) |
| `DATABASE_URL` | SQLAlchemy URL; SQLite in the data folder by default, `postgresql+psycopg://…` with `[postgres]` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_URL`, `GITHUB_API_URL`, `GITHUB_SCOPES` | the GitHub login |
| `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_URL`, `GITLAB_SCOPES` | the GitLab login |
| `MORTAR_ALLOW_USERS` | who may sign in: `github:mike, gitlab:ann`, or a bare login for either |
| `MORTAR_ALLOW_ORGS` | members of these GitHub orgs or GitLab groups may sign in |
| `MORTAR_ADMINS` | admins (may sign in too): everyone's audit log, plugins for everyone, installs |
| `MORTAR_ALLOW_ANYONE` | `1` lets any account sign in (on purpose only) |
| `MORTAR_IDLE_HOURS`, `MORTAR_SESSION_DAYS` | session idle timeout (12) and lifetime (7) |
| `MORTAR_CACHE` | how long GET answers are cached per user (`60s`); ETags make refreshes cheap |
| `BNB_PLUGIN_INDEX`, `BNB_PLUGIN_INDEX_KEYS` | the plugin index (URL or file) and the keys it must be signed with |
| `MORTAR_PLUGIN_INSTALL` | `1` lets admins install plugins from the marketplace page |
| `MORTAR_TRUSTED_PROXIES` | how many proxies in front set `X-Forwarded-*` (0) |
| `MORTAR_DEV` | `1`: http allowed, plain cookie names (local development only) |

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
- **API keys** (`bnbo_<id>_<secret>`) are stored hashed, have a scope (`read` or `write`), an optional expiry,
  and show their last use. Read keys can still ask for dry runs. Creating keys, deleting the account and
  installing plugins need a browser session, not a key.
- **Device login** (`bnb login URL`): the terminal shows a code, the user approves it in the browser and picks
  the scope; the terminal gets a key. Codes expire after 10 minutes and polling is rate limited.
- **The GitHub proxy** for signed-in CLIs passes on only the reads bnb makes and the writes bnb and its plugins
  make; deleting repos, changing access, keys, hooks and account settings are never passed on.
- **Audit log**: sign-ins, keys, every change (with its output) and every dry run, with who, how and from where.
- **Browser hardening**: a strict CSP (`self` only, no inline scripts or styles, no `data:` fonts), no
  `v-html` anywhere in the UI, `X-Frame-Options: DENY`, `no-referrer`, HSTS, `Cache-Control: no-store` on the API.
- **Throttling** on sign-in, device codes and key use.

## The CLI and TUI through Mortar

```sh
bnb login https://mortar.example.com        # device login; --read for a read-only key, --key KEY to paste one
bnb whoami
bnb status                                # every command now goes through Mortar
bnb tui
bnb logout                                # forgets the key here (revoke it in Settings › API keys)
```

The key is kept in `~/.config/mortar/operator.json` (0600). `BNB_OPERATOR` and `BNB_OPERATOR_KEY`
do the same for CI. The defaults repo and orgs then come from your Mortar settings.

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

The marketplace reads a plugin index (`plugins/index.json` by default) signed with Ed25519 (`bnb market
keygen`, `bnb market sign`). Unsigned or wrongly signed indexes are shown but nothing installs from them.
Installing from the web is off unless `MORTAR_PLUGIN_INSTALL=1`, and only admins can do it; restart
Mortar after an install. The signing code is adapted from labdex (relicensed into Mortar (formerly bitsandbricks)
under MIT by its author).

## API

Everything the UI does is JSON under `/api/v1` and works with an API key (`Authorization: Bearer bnbo_…`):

`/me`, `/repos`, `/status`, `/prs`, `/pr`, `/merge`, `/ready`, `/runs`, `/approvals`, `/approve`, `/releases`,
`/packages`, `/security`, `/doctor`, `/sync`, `/new`, `/metrics?days=`, `/console`, `/defaults…`, `/plugins…`,
`/market…`, `/settings`, `/keys`, `/sessions`, `/secrets`, `/audit`, `/rate`, `/cache/clear`.
Changes take `"dry_run": true`.
