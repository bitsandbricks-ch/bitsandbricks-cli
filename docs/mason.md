# Mason

Mason is mason as a web app: it holds the bricks together. People sign in with their own GitHub and GitLab accounts and get every
CLI and TUI view in a terminal-style browser UI: repos, pull requests, runs, approvals, releases, packages,
issues, security alerts, doctor/fix/sync, workflow hardening, stats, a defaults editor, a `mason` console,
plugins and a plugin marketplace.
The same API serves `mason` and `mason tui` on a laptop: `mason login https://mason` and they need neither gh
nor a GitHub token.

```
browser (Vue, web/)  ─┐                         ┌─> GitHub REST/GraphQL  (the user's own OAuth token)
mason / mason tui (key) ──┼─> /api/v1  (Flask)  ────┤
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
- **Workflows** shows what `mason workflows` finds; **p** opens a PR that pins the selected repo's actions
  to commit SHAs.
- **Settings** holds the defaults repo, orgs, extra and left-out repos, GitLab groups and projects,
  connected accounts, API keys, sessions and plugin secrets.
- **Keys:** arrows or `j`/`k` move, Enter acts, `/` filters, `r` reloads, `t` switches the theme
  (phosphor, amber, paper), `?` lists the keys.

## Run it

Mason needs the `server` extra and the built UI. Installing the package (a wheel or sdist build, as
`pipx install "mason-cli[tui,server] @ git+https://github.com/bitsandbricks-ch/mason-cli"` does)
builds the UI from `web/` into it with `npm ci && npm run build`, so have Node.js (`npm`) on your PATH.
Without npm the install still works, but `mason serve` prints how to build the UI and the browser gets
only that message. `mason serve --check` shows whether the UI is built. `MASON_SKIP_WEB_BUILD=1`
skips the build. An editable install (`pip install -e`) never builds it, so from a clone:

```sh
pip install -e ".[server]"                   # flask, sqlalchemy, cryptography, gunicorn
(cd web && npm ci && npm run build)          # the UI, into src/mason/server/static
export MASON_ADMINS=github:you                 # optional: sign-in works without any settings
mason serve --check                            # what the settings allow, and what is missing
mason serve                                    # http://localhost:8080 (Flask's development server)
mason serve --host 0.0.0.0 --port 9000         # somewhere else
```

`--port` sets the port Mason listens on (default 8080). Without `MASON_URL`, Mason's URL follows it, so
`mason serve --port 9000` is `http://localhost:9000`. Set `MASON_URL` yourself when people reach Mason any
other way, and keep its port the same as in the OAuth apps' callback URLs
(`{MASON_URL}/api/v1/auth/{github|gitlab}/callback`): a GitHub or GitLab app registered for
`http://localhost:8080/...` won't send you back to `:9000`.

UI development: `cd web && npm run dev` serves the UI on :5173 and passes `/api` to Mason on :8080.

## Run it with Docker

CI (`.github/workflows/image.yml`) publishes the image to GitHub Container Registry:

| tag | from |
| --- | --- |
| `ghcr.io/bitsandbricks-ch/mason-cli:main` | every push to `main` |
| `:1.2.3`, `:1.2`, `:latest` | a `v1.2.3` tag |
| `:sha-<commit>` | every build |

`deploy/docker-compose.yml` runs it with Postgres, taking its settings from `deploy/.env`:

```sh
cd deploy
cp .env.example .env        # MASON_URL, the OAuth apps, MASON_ADMINS, POSTGRES_PASSWORD (and MASON_TAG)
docker compose run --rm mason mason serve --check   # what the settings allow, and what is missing
docker compose up -d        # Mason on 127.0.0.1:8080, data in the `data` and `db` volumes
docker compose pull && docker compose up -d        # update to the newest image of MASON_TAG
```

Put a TLS proxy (Caddy, Traefik, nginx) in front that serves `MASON_URL` and forwards to `127.0.0.1:8080`;
compose sets `MASON_TRUSTED_PROXIES=1` so Mason sees the real client address. Without `MASON_ALLOW_*` anyone
with a GitHub or GitLab account can sign in and sees only their own repos and keys. The image is
private until you make the package public in its GitHub settings (or `docker login ghcr.io` first).
To build from the checkout instead of pulling: `docker compose up -d --build`, or `docker build -t mason .`.

The image runs gunicorn with one process and many threads, which keeps each user's API cache in one place.
It runs as an unprivileged user (uid 10001) and keeps its data (SQLite by default, the secret key) in `/data`.
In production use the image: `docker build -t mason .`, or `deploy/docker-compose.yml`
(Mason + Postgres on `127.0.0.1:8080`; copy `deploy/mason.env.example` to `deploy/mason.env`). Put it behind a TLS
proxy and set `MASON_TRUSTED_PROXIES=1` so it sees the real client address. The image runs
gunicorn with one process and many threads, which keeps each user's API cache in one place.

## Signing in

GitHub sign-in works out of the box: Mason ships the client ID of its own GitHub OAuth app
(`Ov23liFJgLtIjB3ZtuOy`; a client ID is public) and signs in with GitHub's device flow, which needs no
client secret. The login page shows a code and a link to <https://github.com/login/device>; once you have
entered the code there, the page signs you in by itself. `GITHUB_CLIENT_ID` uses your own OAuth app
instead (switch on "Enable Device Flow" in its settings).

With a client secret as well (`GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`, or `GITLAB_CLIENT_ID` and
`GITLAB_CLIENT_SECRET`) the sign-in is the usual redirect: the authorization code flow with PKCE, back to
`{MASON_URL}/api/v1/auth/{github|gitlab}/callback`. GitLab always needs its own app. Mason's own
device-code flow for `mason login` is separate (see [The CLI and TUI through
Mason](#the-cli-and-tui-through-mason)). Someone signed in with one provider connects the other in
Settings › Accounts, and both logins then belong to the same Mason user.

Anyone with a GitHub or GitLab account may sign in, and each user sees only their own repos (through
their own token), API keys, sessions, secrets and audit log. `MASON_ALLOW_USERS` and `MASON_ALLOW_ORGS`
restrict who may sign in; `MASON_ADMINS` names the admins, and only it makes anyone an admin (taking a
login off the list takes the admin rights away at the next request).

## OAuth apps

| provider | where | callback URL | scopes |
| --- | --- | --- | --- |
| GitHub | Settings › Developer settings › OAuth Apps | `{MASON_URL}/api/v1/auth/github/callback` | `repo read:org workflow security_events read:packages` |
| GitLab | Preferences (or Admin) › Applications, confidential | `{MASON_URL}/api/v1/auth/gitlab/callback` | `api` |

GitHub Enterprise: set `GITHUB_URL` (and `GITHUB_API_URL` if it isn't `{GITHUB_URL}/api/v3`). Self-managed
GitLab: set `GITLAB_URL`.

## Settings (environment)

| variable | meaning |
| --- | --- |
| `MASON_URL` | where people reach it (default `http://localhost:8080`, or the port from `mason serve --port`); it must match the OAuth apps' callback URLs, port included; plain `http://` only for localhost, private addresses and `.local`, `.lan` or `.home.arpa` names, `https://` everywhere else |
| `MASON_SECRET_KEY` | encrypts stored tokens and secrets; when unset, Mason makes a random one on first run in `MASON_DATA/secret.key` (mode 0600) and keeps it. Never commit it; `data/` is gitignored. Changing it makes stored tokens unreadable, so users sign in again |
| `MASON_DATA` | data folder (default `./data`, `/data` in the image) |
| `DATABASE_URL` | SQLAlchemy URL; SQLite in the data folder by default, `postgresql+psycopg://…` with `[postgres]` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_URL`, `GITHUB_API_URL`, `GITHUB_SCOPES` | the GitHub login: the client ID defaults to Mason's own app (device flow) on github.com; with a secret, the redirect sign-in (scopes default to the table above) |
| `GITLAB_CLIENT_ID`, `GITLAB_CLIENT_SECRET`, `GITLAB_URL`, `GITLAB_SCOPES` | the GitLab login |
| `MASON_ALLOW_USERS` | optional: only these may sign in: `github:mike, gitlab:ann`, or a bare login for either |
| `MASON_ALLOW_ORGS` | optional: only members of these GitHub orgs or GitLab groups may sign in |
| `MASON_ADMINS` | the admins (may always sign in): everyone's audit log, plugins for everyone, installs |
| `MASON_IDLE_HOURS`, `MASON_SESSION_DAYS` | session idle timeout (12) and lifetime (7) |
| `MASON_CACHE` | the base time GitHub and GitLab answers are kept per user (`60s`, `0` to always ask); see [Caching](#caching) |
| `MASON_CACHE_SIZE`, `MASON_CACHE_USER_SIZE` | memory for cached answers, all users together (`256MB`) and one user's share (`32MB`) |
| `MASON_PLUGIN_INDEX`, `MASON_PLUGIN_INDEX_KEYS` | the plugin index (URL or file; default: `plugins/index.json` on this repo's main branch) and extra public keys (base64, comma separated) it may be signed with |
| `MASON_PLUGIN_INSTALL` | `1` lets admins install plugins from the marketplace page |
| `MASON_TRUSTED_PROXIES` | how many proxies in front set `X-Forwarded-*` (0) |

Over http the session cookie loses its `__Host-` prefix. Mason refuses to start when no login is
configured (GitHub Enterprise without `GITHUB_CLIENT_ID`, say) or the URL is plain http on a public name.

## Caching

Mason keeps GitHub and GitLab answers in memory only, never on disk, and each user's in their own
cache: nothing one user fetched is ever looked up for another.

- **Per resource**: PRs, merge requests, issues, CI runs, checks and pipelines (and GraphQL) are kept 30s
  at most; files, labels, rulesets and other repo settings for `MASON_CACHE`; the account, its orgs and
  repo metadata 5x as long. `/rate_limit` and job logs are never served from the cache.
- **Conditional requests**: an answer past its time is asked for again with its `ETag` (or `Last-Modified`).
  An unchanged answer comes back as 304, which costs no rate limit on GitHub.
- **Writes**: a change made through Mason (the web app, the console, or the CLI through the operator's
  proxy) makes that repo's answers stale at once, and a GraphQL mutation all GraphQL answers. A change to a
  repo itself drops the repo lists, a file change the loaded `mason.toml`.
- **Rate limits**: Mason reads GitHub's `X-RateLimit-*` (per bucket: REST, GraphQL, search) and GitLab's
  `RateLimit-*` headers. Under 5% left, answers are kept 4x as long. At 0, or after a `Retry-After`,
  Mason answers from the cache, even stale, and refuses new calls until the reset without asking.
- **Bounded**: the least recently used answers go first when a user's share or the total is full.
  `/api/v1/rate` shows each user's hits, 304s, entries and bytes; `POST /api/v1/cache/clear` empties it.

## Security model

- **Sign-in**: OAuth authorization code with PKCE (S256) and a `state` bound to the browser by a short-lived
  cookie, or GitHub's device flow, where GitHub's device code stays on the server and the browser holds
  only an HttpOnly cookie for that one sign-in. With allow lists, only allowed accounts get in; the
  decision is made at every sign-in, not once.
- **The user's own token, always.** Every GitHub and GitLab call is made with the signed-in user's token, so
  nobody sees or changes more than their account may. The server never falls back to a token or gh login of
  its own. Tokens are encrypted at rest (Fernet), refreshed when they expire (GitLab), and revoked at the
  provider when someone disconnects a login or deletes their account.
- **Sessions** are random cookies (`__Host-` prefixed, `Secure`, `HttpOnly`, `SameSite=Lax`); the database
  keeps only their SHA-256. Idle and absolute timeouts; users see and end their other sessions.
- **CSRF**: every change needs the session's `X-CSRF-Token` header and a matching `Origin`.
- **API keys** (`mason_<id>_<secret>`) are stored hashed, have a scope (`read` or `write`), an optional expiry,
  and show their last use. Read keys can still ask for dry runs. Creating keys, deleting the account and
  installing plugins need a browser session, not a key.
- **Device login** (`mason login URL`): the terminal shows a code, the user approves it in the browser and picks
  the scope; the terminal gets a key. Codes expire after 10 minutes and polling is rate limited.
- **The GitHub proxy** for signed-in CLIs passes on only the reads mason makes and the writes mason and its plugins
  make; deleting repos, changing access, keys, hooks and account settings are never passed on.
- **Audit log**: sign-ins, keys, every change (with its output) and every dry run, with who, how and from where.
- **Browser hardening**: a strict CSP (`self` only, no inline scripts or styles, no `data:` fonts), no
  `v-html` anywhere in the UI, `X-Frame-Options: DENY`, `no-referrer`, HSTS, `Cache-Control: no-store` on the API.
- **Throttling** on sign-in, device codes and key use.

## The CLI and TUI through Mason

```sh
mason login https://mason.mortar.ch        # device login; --read for a read-only key, --key KEY to paste one
mason whoami
mason status                                # every command now goes through Mason
mason tui
mason logout                                # forgets the key here (revoke it in Settings › API keys)
```

`mason login` asks Mason for a code and opens the approval page in the browser (over SSH it prints the
link), where you pick read only or read and change; the terminal then gets the key. The key is kept in `~/.config/mason/operator.json` (0600). `MASON_SERVER` and `MASON_KEY`
do the same for CI; `MASON_SERVER=` (set but empty) goes back to gh without logging out. The defaults repo
and orgs then come from your Mason settings.

## Defaults

The defaults repo is the same `mason.toml` the CLI reads (see the README). The defaults editor shows
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

Plugins are the same Python entry points the CLI loads. In Mason they can also add:

- `web_actions()`: buttons with parameters (`write: True` actions ask first, are audited, and honor dry run),
- `web_commands`: commands the console allows,
- `web_secrets()`: per-user secrets (e.g. a Hubra token) that users set in Settings and plugins read with
  `plugins.secret(name)` while serving that user.

Each user switches plugins on or off for themselves; admins can switch one off for everyone.

The marketplace reads a plugin index (this repo's `plugins/index.json` by default) signed with Ed25519 (`mason market
keygen`, `mason market sign`). Unsigned or wrongly signed indexes are shown but nothing installs from them.
Installing from the web is off unless `MASON_PLUGIN_INSTALL=1`, and only admins can do it; restart
Mason after an install. The signing code is adapted from labdex (relicensed into Mason under MIT by its author).

## API

Everything the UI does is JSON under `/api/v1` and works with an API key (`Authorization: Bearer mason_…`):

`/me`, `/config`, `/repos`, `/repo`, `/status`, `/prs`, `/pr`, `/merge`, `/ready`, `/issues`, `/issue`,
`/runs`, `/approvals`, `/approve`, `/releases`, `/releases/jobs`, `/packages`, `/security`, `/doctor`, `/sync`,
`/workflows`, `/workflows/pin`, `/new`, `/metrics?days=`, `/console`, `/defaults…`, `/plugins…`, `/market…`,
`/settings`, `/keys`, `/sessions`, `/secrets`, `/audit`, `/rate`, `/cache/clear`, and for admins
`/admin/users` and `/admin/stats`. `/api/v1/health` needs no sign-in. Changes take `"dry_run": true`.
