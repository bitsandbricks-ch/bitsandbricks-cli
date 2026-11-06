"""Plugins: other packages that add commands, status columns, doctor checks and TUI tabs.

A plugin is a Python package with an entry point in the `mortar.plugins` group:

    [project.entry-points."mortar.plugins"]
    hubra = "hubra_cli.mtr:HubraPlugin"

The entry point names a class (or an instance) like `Plugin` below. Every hook is
optional; override the ones you need. Install a plugin next to mtr:

    pipx inject mortar-cli <plugin package>

MTR_PLUGINS=none (or a comma separated list of names) limits which plugins load.
"""

from __future__ import annotations

import contextvars
import os
import sys
from typing import Any, Callable, ClassVar

GROUP = "mortar.plugins"
# The signed-in user's secrets for plugins (the operator sets it per request): plugins.secret("hubra_token").
SECRETS: contextvars.ContextVar = contextvars.ContextVar("mtr_plugin_secrets", default=None)


def secret(name: str) -> str | None:
    """A secret of the current user (in the operator), or None: plugins fall back to their own config."""
    found = SECRETS.get()
    return (found or {}).get(name) or None


class Plugin:
    """Base class for plugins. `mtr plugins` lists the installed ones."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # Extra GraphQL fields for each repo node of `mtr status` and the TUI, e.g.
    # 'manifest: object(expression: "HEAD:plugin.yaml") { ... on Blob { text } }'
    graphql_fields: ClassVar[str] = ""
    # Commands of add_commands that are safe in the operator's web console (no local files, no prompts).
    web_commands: ClassVar[tuple] = ()

    def add_commands(self, sub) -> dict[str, Callable[[Any], None]]:
        """Add argparse subparsers to `sub`; return {command: handler(args)}."""
        return {}

    def status_columns(self) -> list[tuple[str, Callable[[str, dict], str]]]:
        """Extra `mtr status` columns: (header, fn(repo, graphql node) -> cell)."""
        return []

    def doctor(self, repo: str, group, ctx: dict) -> list[str]:
        """More doctor problems for one repo (group is its config.Group, or None)."""
        return []

    def doctor_hints(self, repo: str, group, problems: list[str]) -> list[str]:
        """`fix: ...` lines for the problems this plugin reported."""
        return []

    def repo_detail(self, repo: str, node: dict) -> list[str]:
        """More lines for a repo's detail pane in the TUI (node: its GraphQL node)."""
        return []

    def tui_tabs(self) -> list[dict]:
        """Extra TUI tabs, each a dict:
            id, title     the tab
            rows          [(key, label)] in its left table
            text(key)     what the detail pane shows for a row (runs in a worker thread)
            url(key)      optional: the link Enter/o opens for a row
        """
        return []

    def tui_checks(self) -> list[tuple[str, str, Callable[[list[str]], str]]]:
        """Extra rows of the Checks tab: (key, label, run(repos) -> text)."""
        return []

    def tui_keys(self) -> list[tuple[str, str, Callable[[Any], None]]]:
        """Extra TUI keys: (key, description, run(app)). app.selected_repo(), app.confirm_then(question,
        job, *args), app.do_job(job, *args), app.log_line(text) and app.ask/app.pick are there to use."""
        return []

    def tui_commands(self) -> list[tuple[str, str, Callable[[Any], None]]]:
        """(title, help, run(app)) for the command palette (ctrl+p)."""
        return []

    def tui_themes(self) -> list:
        """Textual Theme objects; t cycles through every theme."""
        return []

    # ── the operator (web app) ──────────────────────────────────────────────
    # tui_tabs, tui_checks, status_columns, repo_detail and doctor work there as they are.

    def web_actions(self) -> list[dict]:
        """Buttons in the web UI, each a dict:
            id, title, help   the button
            repo              True: works on the selected repo (run gets it), False: on nothing
            write             True: changes something (needs a write key, asks first, has a dry run)
            params            [{"name", "label", "choices": [...] (optional)}] asked before running
            run(repo, params) does it; print or core.say what happened (core.dry_run() tells a dry run)
        """
        return []

    def web_secrets(self) -> list[tuple[str, str]]:
        """Secrets a user may keep in the operator for this plugin: [(name, label)]; read with secret(name)."""
        return []


_plugins: list[Plugin] | None = None
errors: list[str] = []


def load() -> list[Plugin]:
    global _plugins
    if _plugins is not None:
        return _plugins
    from importlib.metadata import entry_points

    eps = entry_points()
    found, seen = [], set()
    for group in (GROUP,):
        got = eps.select(group=group) if hasattr(eps, "select") else eps.get(group, [])  # 3.9 returns a dict
        for ep in got:
            if ep.name not in seen:
                seen.add(ep.name)
                found.append(ep)
    _plugins = []
    only = os.environ.get("MTR_PLUGINS")  # unset: all; "none": none; "a,b": only these
    allowed = None if only is None else {n.strip() for n in only.split(",") if n.strip()}
    for ep in sorted(found, key=lambda e: e.name):
        if allowed is not None and ep.name not in allowed:
            continue
        try:
            obj = ep.load()
            plugin = obj() if isinstance(obj, type) else obj
        except Exception as err:  # a broken plugin must not break mtr
            errors.append(f"{ep.name}: {err}")
            continue
        if not getattr(plugin, "name", ""):
            plugin.name = ep.name
        _plugins.append(plugin)
    return _plugins


def register(plugin: Plugin) -> None:
    """Add a plugin by hand (tests, or a script that drives mtr)."""
    load().append(plugin)


def hook(name: str, *args) -> list:
    """Call one hook on every plugin, collecting list answers; a failing plugin adds a line instead."""
    out: list = []
    for p in load():
        fn = getattr(p, name, None)
        if fn is None:
            continue
        try:
            got = fn(*args)
        except Exception as err:
            print(f"plugin {p.name}: {name} failed: {err}", file=sys.stderr)
            continue
        if isinstance(got, list):
            out += got
    return out
