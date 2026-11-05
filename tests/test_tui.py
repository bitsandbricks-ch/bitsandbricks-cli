"""The TUI driven headless against tests/fake_gh (skipped without Textual)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")


async def until(pilot, check, seconds: float = 20) -> None:
    for _ in range(int(seconds * 4)):
        await pilot.pause(0.25)
        if check():
            return


def test_tui_flow(env, monkeypatch):
    for key in ("PATH", "FAKE_GH_LOG", "XDG_CONFIG_HOME", "BNB_DEFAULTS", "BNB_OPEN"):
        monkeypatch.setenv(key, env[key])
    from bitsandbricks import config, core, plugins, tui
    from bitsandbricks.plugins import Plugin

    class Demo(Plugin):
        name = "demo"

        def tui_tabs(self):
            return [{"id": "demo", "title": "Demo", "rows": [("one", "One")], "text": lambda k: f"demo {k}",
                     "url": lambda k: f"https://example.org/{k}"}]

        def tui_keys(self):
            return [("x", "Demo key", lambda app: app.log_line(f"demo key on {app.selected_repo()}"))]

    plugins.load()
    plugins.register(Demo())
    core.DRY_RUN = True
    cfg = config.load()

    async def run():
        app = tui.BnbTui(cfg, dry_run=True)
        async with app.run_test(size=(160, 45)) as pilot:
            table = app.query_one("#repos-table")
            await until(pilot, lambda: table.row_count)
            assert table.row_count == 6
            table.move_cursor(row=table.get_row_index("acme/web"))
            await pilot.press("x")
            await pilot.pause(0.3)
            await pilot.press("right")
            await pilot.pause(0.3)
            assert app.tab() == "prs"
            assert app.query_one("#prs-table").row_count == 3
            await pilot.press("3")
            runs = app.query_one("#runs-table")
            await until(pilot, lambda: runs.row_count)
            runs.move_cursor(row=runs.get_row_index("acme/api|5"))
            await pilot.press("a")
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(1)
            await pilot.press("5")
            await until(pilot, lambda: "…" not in str(app.query_one("#checks-detail").render()))
            assert "LICENSE differs from plugin-tpl" in str(app.query_one("#checks-detail").render())
            await pilot.press("6")
            await until(pilot, lambda: "demo one" in str(app.query_one("#demo-detail").render()))
            await pilot.press("o")
            await pilot.pause(0.3)
            log = [str(line.text) for line in app.query_one("#log").lines]
            assert any("demo key on acme/web" in line for line in log), log
            assert any("[dry-run] approve acme/api main" in line for line in log), log
            assert any("https://example.org/one" in line for line in log), log

    asyncio.run(run())
