# Backlog

Ideas and features for bnb and Mortar (the web app), roughly in the order they'd pay off. Pick one, open a PR,
move it to "Done" when it ships.

## Next

- **hubra-cli on the operator.** hubra-cli still has its own gh plumbing and token file, so in the
  operator its commands don't run with the signed-in user's token. Port it to `mortar.core`
  (transport-aware `api`/`graphql`), read the Hubra token with `plugins.secret("hubra_token")`, and expose
  `web_secrets()`, `web_actions()` (sign, release, verify) and `web_commands`. Follow-up PR in hubra-cli.
- **GitLab in the CLI and TUI.** The operator speaks GitLab; `bnb status/prs/runs/doctor` on a laptop still
  only know GitHub. Move `operator/gitlab.py` behind the transport so the CLI can use a GitLab token or the
  operator.
- **Signed official plugin index.** Make the project key, sign `plugins/index.json` in CI, ship the public
  key in `src/mortar/keys/`.
- **Live updates.** GitHub/GitLab webhooks (or a GitHub App) into the operator, pushed to the browser with
  server-sent events, so lists refresh themselves and the cache can be invalidated precisely.
- **Scheduled checks.** Run `doctor` nightly per user or group, keep the results, and show drift over time;
  notify on new drift (email, Slack, Discord, a webhook).
- **Access review.** Who has admin or write on each repo, outside collaborators, deploy keys and webhooks
  (read only), with a periodic "still needed?" review and an export for audits.
- **Prometheus `/metrics`** for the operator itself (requests, GitHub calls, cache hit rate, rate limit left)
  and an optional exporter for repo stats.

## Defaults and rules

- Defaults stored in the operator's database as an alternative to a defaults repo, with history and review.
- Policy packs: shareable sets of groups, rulesets and files (e.g. "OpenSSF baseline", "small OSS project").
- Visual ruleset editor next to the JSON one; diff live ruleset vs defaults before applying.
- Org-level settings (default repo permissions, Actions policies, required workflows) in `[org]` tables.
- CODEOWNERS and branch naming rules checked by doctor.
- Environments in mortar.toml: required reviewers, wait timers, which branches may deploy.
- Secrets and variables by name only: which repos lack a secret the group needs, and which are unused.
- The new repo keys (topics, labels, required files) for GitLab groups too.
- Terraform / OpenTofu export of the defaults, for teams that manage GitHub as code.
- Exceptions with an expiry ("repo X may skip rule Y until 2027-01-01, because …").
- Template repos as a first-class concept: "new repo from template" wizard with variables.

## Metrics and stats

- DORA-style numbers: deployment frequency, lead time, change failure rate, time to restore.
- PR review latency (time to first review), reviewer load, PR size distribution.
- Flaky workflow detection (same commit, different outcome) and slowest jobs.
- Long-term history kept in the database so charts go back further than the APIs do.
- CSV/JSON export and a shareable read-only dashboard link.
- Actions cost: minutes, cache and artifact storage per repo and workflow, and the biggest savings.
- Supply chain per release: SBOM, signed provenance (`gh attestation`), and which releases lack them.

## Mortar

- Teams and shared workspaces: a team's defaults, repo lists and saved views, with roles.
- A daily digest (email or chat): PRs waiting for you, failed runs, new alerts, new drift.
- A command palette (Ctrl+K) in the web UI that searches repos, PRs and commands at once.
- Repo ownership: an owner per repo (from CODEOWNERS or a topic), last activity, and archive candidates.
- Saved views and filters (e.g. "my PRs needing review across both providers").
- Bulk actions with a preview: rerun failed runs, update a file in N repos, archive stale repos.
- OIDC / SAML sign-in for the operator itself in companies, next to GitHub and GitLab.
- GitHub App mode (installation tokens, fine-grained permissions) as an alternative to OAuth apps.
- Passkeys as a second factor for admin actions.
- Per-user rate limit budget and a queue for big jobs, with progress in the UI.
- Redis-backed cache and sessions to run several operator processes.
- Helm chart and a k8s manifest next to the docker-compose example.

## Plugins

- Kubernetes (k3s) plugin: deployments per repo, image versions vs latest release.
- Docker/registry plugin: image ages, vulnerable base images, cleanup of old tags.
- Renovate/Dependabot dashboard: open update PRs by repo, auto-merge policy.
- Sentry/uptime plugin: link errors and incidents to repos and releases.
- Plugin sandboxing: run plugins in a subprocess with only the API calls they declare.
- Plugin settings UI generated from a schema the plugin declares.

## AI

- An agent that proposes fixes as PRs: security alerts, failing workflows, merge conflicts, drift that
  `doctor --fix` can't fix (e.g. missing files that need content).
- "Explain this failure": summarize a failed run's logs next to the run.
- Natural-language console (`bnb ask "which repos lack branch protection?"`) that turns into bnb commands,
  shown before they run.

## TUI and CLI

- `bnb tui` themes matching the web UI (phosphor, amber, paper).
- `bnb watch`: a live status line for a set of repos.
- Shell completion for repo names and groups.

## Done

- Workflow hardening: `bnb workflows [--fix]`, doctor's `workflows` check and the web app's Workflows tab.
- More repo settings in mortar.toml: topics, labels, Actions permissions, required files and `skip`.
- Mortar, the web app, with GitHub and GitLab OAuth, terminal UI, API keys, device login (this PR).
- Plugin marketplace with a signed index; `bnb stats`; transport layer with caching.
