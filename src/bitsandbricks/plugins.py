"""Plugins: other packages that add commands, status columns, doctor checks and TUI tabs.

A plugin is a Python package with an entry point in the `bitsandbricks.plugins` group:

    [project.entry-points."bitsandbricks.plugins"]
    hubra = "hubra_cli.bnb:HubraPlugin"

The entry point names a class (or an instance) like `Plugin` below. Every hook is
optional; override the ones you need. Install a plugin next to bnb:

    pipx inject bitsandbricks-cli <plugin package>

BNB_PLUGINS=none (or a comma separated list of names) limits which plugins load.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, ClassVar

GROUP = "bitsandbricks.plugins"


class Plugin:
    """Base class for plugins. `bnb plugins` lists the installed ones."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    # Extra GraphQL fields for each repo node of `bnb status` and the TUI, e.g.
    # 'manifest: object(expression: "HEAD:plugin.yaml") { ... on Blob { text } }'
    graphql_fields: ClassVar[str] = ""

    def add_commands(self, sub) -> dict[str, Callable[[Any], None]]:
        """Add argparse subparsers to `sub`; return {command: handler(args)}."""
        return {}

    def status_columns(self) -> list[tuple[str, Callable[[str, dict], str]]]:
        """Extra `bnb status` columns: (header, fn(repo, graphql node) -> cell)."""
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


_plugins: list[Plugin] | None = None
errors: list[str] = []


def load() -> list[Plugin]:
    global _plugins
    if _plugins is not None:
        return _plugins
    from importlib.metadata import entry_points

    eps = entry_points()
    found = eps.select(group=GROUP) if hasattr(eps, "select") else eps.get(GROUP, [])  # 3.9 returns a dict
    _plugins = []
    only = os.environ.get("BNB_PLUGINS")  # unset: all; "none": none; "a,b": only these
    allowed = None if only is None else {n.strip() for n in only.split(",") if n.strip()}
    for ep in sorted(found, key=lambda e: e.name):
        if allowed is not None and ep.name not in allowed:
            continue
        try:
            obj = ep.load()
            plugin = obj() if isinstance(obj, type) else obj
        except Exception as err:  # a broken plugin must not break bnb
            errors.append(f"{ep.name}: {err}")
            continue
        if not getattr(plugin, "name", ""):
            plugin.name = ep.name
        _plugins.append(plugin)
    return _plugins


def register(plugin: Plugin) -> None:
    """Add a plugin by hand (tests, or a script that drives bnb)."""
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
