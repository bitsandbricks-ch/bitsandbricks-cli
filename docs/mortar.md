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

Mortar needs the `server` extra and the built UI. Installing the package (a wheel or sdist build, as
`pipx install "mortar-cli[tui,server] @ git+https://github.com/bitsandbricks-ch/mortar-cli"` does)
builds the UI from `web/` into it with `npm ci && npm run build`, so have Node.js (`npm`) on your PATH.
Without npm the install still works, but `mtr serve` prints how to build the UI and the browser gets
only that message. `mtr serve --check` shows whether the UI is built. `MTR_SKIP_WEB_BUILD=1`
skips the build. An editable install (`pip install -e`) never builds it, so from a clone:

```sh
pip install -e ".[server]"                   # flask, sqlalchemy, cryptography, gunicorn
(cd web && npm ci && npm run build)          # the UI, into src/mortar/server/static
export MTR_ADMINS=github:you                 # optional: sign-in works without any settings
mtr serve --check                            # what the settings allow, and what is missing
mtr serve                                    # http://localhost:8080 (Flask's development server)
mtr serve --host 0.0.0.0 --port 9000         # somewhere else
```

`--port` sets the port Mortar listens on (default 8080). Without `MTR_URL`, Mortar's URL follows it, so
`mtr serve --port 9000` is `http://localhost:9000`. Set `MTR_URL` yourself when people reach Mortar any
other way, and keep its port the same as in the OAuth apps' callback URLs
(`{MTR_URL}/api/v1/auth/{github|gitlab}/callback`): a GitHub or GitLab app registered for
`http://localhost:8080/...` won't send you back to `:9000`.

UI development: `cd web && npm run dev` serves the UI on :5173 and passes `/api` to Mortar on :8080.

## Run it with Docker

CI (`.github/workflows/image.yml`) publishes the image to GitHub Container Registry:

| tag | from |
| --- | --- |
| `ghcr.io/bitsandbricks-ch/mortar-cli:main` | every push to `main` |
| `:1.2.3`, `:1.2`, `:latest` | a `v1.2.3` tag |
| `:sha-<commit>` | every build |

`deploy/docker-compose.yml` runs it with Postgres, taking its settings from `deploy/.env`:

```sh
cd deploy
cp .env.example .env        # MTR_URL, the OAuth apps, MTR_ADMINS, POSTGRES_PASSWORD (and MTR_TAG)
docker compose run --rm mortar mtr serve --check   # what the settings allow, and what is missing
docker compose up -d        # Mortar on 127.0.0.1:8080, data in the `data` and `db` volumes
docker compose pull && docker compose up -d        # update to the newest image of MTR_TAG
```

Put a TLS proxy (Caddy, Traefik, nginx) in front that serves `MTR_URL` and forwards to `127.0.0.1:8080`;
compose sets `MTR_TRUSTED_PROXIES=1` so Mortar sees the real client address. Without `MTR_ALLOW_*` anyone
with a GitHub or GitLab account can sign in and sees only their own repos and keys. The image is
private until you make the package public in its GitHub settings (or `docker login ghcr.io` first).
To build from the checkout instead of pulling: `docker compose up -d --build`, or `docker build -t mortar .`.

The image runs gunicorn with one process and many threads, which keeps each user's API cache in one place.
It runs as an unprivileged user (uid 10001) and keeps its data (SQLite by default, the secret key) in `/data`.
In production use the image: `docker build -t mortar .`, or `deploy/docker-compose.yml`
(Mortar + Postgres on `127.0.0.1:8080`; copy `deploy/mortar.env.example` to `deploy/mortar.env`). Put it behind a TLS
proxy and set `MTR_TRUSTED_PROXIES=1` so it sees the real client address. The image runs
gunicorn with one process and many threads, which keeps each user's API cache in one place.

## Signing in

GitHub sign-in works out of the box: Mortar ships the client ID of its own GitHub OAuth app
(`Ov23liFJgLtIjB3ZtuOy`; a client ID is public) and signs in with GitHub's device flow, which needs no
client secret. The login page shows a code and a link to <https://github.com/login/device>; once you have
entered the code there, the page signs you in by itself. `GITHUB_CLIENT_ID` uses your own OAuth app
instead (switch on "Enable Device Flow" in its settings).

With a client secret as well (`GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, or `GITLAB_CLIENT_ID` and
`GITLAB_CLIENT_SECRET`) the sign-in is the usual redirect: the authorization code flow with PKCE, back to
`{MTR_URL}/api/v1/auth/{github|gitlab}/callback`. GitLab always needs its own app. Mortar's own
device-code flow for `mtr login` is separate (see [The CLI and TUI through
Mortar](#the-cli-and-tui-through-mortar)). Someone signed in with one provider connects the other in
Settings › Accounts, and both logins then belong to the same Mortar user.

Anyone with a GitHub or GitLab account may sign in, and each user sees only their own repos (through
their own token), API keys, sessions, secrets and audit log. `MTR_ALLOW_USERS` and `MTR_ALLOW_ORGS`
restrict who may sign in; `MTR_ADMINS` names the admins, and only it makes anyone an admin (taking a
login off the list takes the admin rights away at the next request).

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
| `MTR_URL` | where people reach it (default `http://localhost:8080`, or the port from `mtr serve --port`); it must match the OAuth apps' callback URLs, port included; plain `http://` only for localhost, private addresses and `.local`, `.lan` or `.home.arpa` names, `https://` everywhere else |
| `MTR_SECRET_KEY` | encrypts stored tokens and secrets; when unset, Mortar makes a random one on first run in `MTR_DATA/secret.key` (mode 0600) and keeps it. Never commit it; `data/` is gitignored. Changing it makes stored tokens unreadable, so users sign in again |
| `MTR_DATA` | data folder (default `./data`, `/data` in the image) |
| `DATABASE_URL` | SQLAlchemy URL; SQLite in the data folder by default, `postgresql+psycopg://…` with `[postgres]` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_URL`, `GITHUB_API_URL`, `GITHUB_SCOPES` | the GitHub login: the client ID defaults to Mortar's own app (device flow) on github.com; with a secret, the redirect sign-in (scopes default to the table above) |
| `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_URL`, `GITLAB_SCOPES` | the GitLab login |
| `MTR_ALLOW_USERS` | optional: only these may sign in: `github:mike, gitlab:ann`, or a bare login for either |
| `MTR_ALLOW_ORGS` | optional: only members of these GitHub orgs or GitLab groups may sign in |
| `MTR_ADMINS` | the admins (may always sign in): everyone's audit log, plugins for everyone, installs |
| `MTR_IDLE_HOURS`, `MTR_SESSION_DAYS` | session idle timeout (12) and lifetime (7) |
| `MTR_CACHE` | how long GET answers are cached per user (`60s`); ETags make refreshes cheap |
| `MTR_PLUGIN_INDEX`, `MTR_PLUGIN_INDEX_KEYS` | the plugin index (URL or file; default: `plugins/index.json` on this repo's main branch) and extra public keys (base64, comma separated) it may be signed with |
| `MTR_PLUGIN_INSTALL` | `1` lets admins install plugins from the marketplace page |
| `MTR_TRUSTED_PROXIES` | how many proxies in front set `X-Forwarded-*` (0) |
| `MTR_AUDIT_DAYS`, `MTR_AUDIT_ROWS`, `MTR_AUDIT_OUTPUT` | audit rows go after 90 days; each user keeps the newest 1000; command output is cut to 1000 characters |
| `MTR_RATE_API`, `MTR_RATE_WRITES`, `MTR_RATE_ANON` | API calls per minute: per user (600), changes per user (60), signed-out calls per address (120); 0 switches one off. Reads through the GitHub proxy for the CLI aren't counted (they spend only your own GitHub quota) |
| `MTR_SIGNUPS_PER_HOUR`, `MTR_SIGNUPS_PER_IP`, `MTR_MAX_USERS` | new accounts per hour in all (60) and per address (5), and the most accounts this Mortar takes (1000; 0 = no cap); admins from `MTR_ADMINS` always get in |
| `MTR_MAX_KEYS`, `MTR_MAX_SESSIONS` | working API keys (50) and browser sessions (20) per user |
| `MTR_AUDIT_IPS` | what the audit log keeps of the caller's address: `hash` (default: an HMAC with the secret key, so "same address" shows without the address), `none`, or `full` |

Over http the session cookie loses its `__Host-` prefix. Mortar refuses to start when no login is
configured (GitHub Enterprise without `GITHUB_CLIENT_ID`, say) or the URL is plain http on a public name.

## Security model

- **Sign-in**: OAuth authorization code with PKCE (S256) and a `state` bound to the browser by a short-lived
  cookie, or GitHub's device flow, where GitHub's device code stays on the server and the browser holds
  only an HttpOnly cookie for that one sign-in. With allow lists, only allowed accounts get in; the
  decision is made at every sign-in, not once.
- **The user's own token, always.** Every GitHub and GitLab call is made with the signed-in user's token, so
  nobody sees or changes more than their account may. The server never falls back to a token or gh login of
  its own. Tokens are encrypted at rest (Fernet) and refreshed when they expire (GitLab).
- **Revoking tokens.** When someone disconnects a login or deletes their account, Mortar deletes its copy of
  the token and asks the provider to revoke it. Both GitHub's and GitLab's revoke APIs need the OAuth app's
  client secret, so this works only with `GITHUB_CLIENT_SECRET` (GitHub) and always for GitLab, which
  needs its secret anyway. With the built-in GitHub sign-in (device flow, no secret) Mortar **cannot**
  revoke the token: the UI then shows the link to the app's page at GitHub
  (`https://github.com/settings/connections/applications/<client id>`), where one click revokes it, and the
  audit log records that it wasn't revoked. A failed revoke call is handled the same way.
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
- **Audit log**: sign-ins, keys, every change (with its output) and every dry run, with who and how. It stays
  bounded: the caller's address is kept only as a keyed hash (`MTR_AUDIT_IPS`), output is cut to
  `MTR_AUDIT_OUTPUT` characters, each user keeps at most `MTR_AUDIT_ROWS` rows, rows go after
  `MTR_AUDIT_DAYS`, and deleting an account deletes its rows (one row without name or address records the
  deletion). Expired sessions, sign-in states, device codes and keys revoked or expired over 30 days ago are
  deleted every few minutes.
- **Browser hardening**: a strict CSP (`self` only, no inline scripts or styles, no `data:` fonts), no
  `v-html` anywhere in the UI, `X-Frame-Options: DENY`, `no-referrer`, HSTS, `Cache-Control: no-store` on the API.
- **Throttling and caps** so open sign-in can't fill the disk: sign-ins, device codes, code lookups and bad
  keys are throttled per address; API calls per user (and per address when signed out); new accounts per
  hour, per address and in all (`MTR_MAX_USERS`); and what each user stores is capped (below). Admins from
  `MTR_ADMINS` always get an account. Counters live in memory, so they start over on restart, and each
  server process counts on its own (the image runs one).

  | Per user | Cap |
  |---|---|
  | API keys | `MTR_MAX_KEYS` (50) working keys; revoked or expired ones are deleted after 30 days or beyond twice that number |
  | sessions | `MTR_MAX_SESSIONS` (20); the oldest ends when a new one starts |
  | settings | 32 KB |
  | secrets | one per name a plugin declares, 4000 characters each |
  | audit log | `MTR_AUDIT_ROWS` (1000) rows of at most ~1.5 KB |

  So one account takes at most about 2 MB, and the database at most about `MTR_MAX_USERS` × 2 MB (2 GB with
  the defaults) plus one shared audit budget for failed sign-ins.

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
