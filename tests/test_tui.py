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
    for key in ("PATH", "FAKE_GH_LOG", "XDG_CONFIG_HOME", "MASON_DEFAULTS", "MASON_OPEN", "MASON_PLUGINS"):
        monkeypatch.setenv(key, env[key])
    from mason import config, core, plugins, tui
    from mason.plugins import Plugin

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
        app = tui.MasonTui(cfg, dry_run=True)
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
            prs = app.query_one("#prs-table")
            prs.move_cursor(row=prs.get_row_index("acme/web#7"))
            await pilot.pause(0.2)
            assert "u update branch" in str(app.query_one("#prs-detail").render())
            await pilot.press("u")
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(0.5)
            await pilot.press("c")
            await pilot.pause(0.3)
            await pilot.press(*"thanks", "enter")
            await pilot.pause(0.5)
            await pilot.press("3")
            runs = app.query_one("#runs-table")
            await until(pilot, lambda: runs.row_count)
            runs.move_cursor(row=runs.get_row_index("acme/api|5"))
            await pilot.press("a")
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(1)
            runs.move_cursor(row=runs.get_row_index("acme/plugin-a|12"))
            await pilot.press("a")
            await pilot.pause(0.2)
            runs.move_cursor(row=runs.get_row_index("acme/plugin-b|7"))
            await until(pilot, lambda: "cosign" in str(app.query_one("#runs-detail").render()))
            assert "l failed log" in str(app.query_one("#runs-detail").render())
            await pilot.press("l")
            await until(pilot, lambda: "createLogEntryConflict" in str(app.query_one("#runs-detail").render()))
            await pilot.press("x")
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(1)
            await pilot.press("4")
            releases = app.query_one("#releases-table")
            await until(pilot, lambda: releases.row_count)
            assert releases.row_count == 3 and app.tab() == "releases"
            releases.move_cursor(row=releases.get_row_index("acme/plugin-b"))
            await until(pilot, lambda: "cosign" in str(app.query_one("#releases-detail").render()))
            assert "MISSING *.zip" in str(app.query_one("#releases-detail").render())
            await until(pilot, lambda: "→ R releases v0.4.0" in str(app.query_one("#releases-detail").render()))
            assert "b bump version" in str(app.query_one("#releases-detail").render())
            await pilot.press("R")
            await until(pilot, lambda: "v0.4.0" in str(app.screen.query_one("Label").render()))
            await pilot.press("y")
            await pilot.pause(1)
            await pilot.press("b")
            await pilot.pause(0.3)
            await pilot.press("down", "enter")
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(1)
            releases.move_cursor(row=releases.get_row_index("acme/plugin-b"))
            await pilot.pause(0.2)
            await pilot.press("enter")  # the release page
            await until(pilot, lambda: "Release v0.3.0" in str(app.screen.query_one("#page-detail").render()))
            assert "- Faster startup" in str(app.screen.query_one("#page-detail").render())
            await pilot.press("escape")
            await pilot.pause(0.3)
            await pilot.press("N")
            await until(pilot, lambda: "New release of acme/plugin-b" in str(app.screen.query_one("Label").render()))
            assert app.screen.query_one("Input").value == "v0.3.1"
            await pilot.press("enter")
            await pilot.pause(0.3)
            await pilot.press("enter")  # the title stays the tag
            await pilot.pause(0.3)
            await pilot.press("enter")  # no notes: GitHub writes them
            await pilot.pause(0.3)
            await pilot.press("down", "down", "enter")  # draft
            await pilot.pause(0.3)
            await pilot.press("y")
            await pilot.pause(1)
            await pilot.press("T")
            await until(pilot, lambda: "New tag on acme/plugin-b" in str(app.screen.query_one("Label").render()))
            await pilot.press("enter")
            await until(pilot, lambda: "release.yml creates" in str(app.screen.query_one("Label").render()))
            await pilot.press("y")
            await pilot.pause(1)
            await pilot.press("5")
            images = app.query_one("#packages-table")
            await until(pilot, lambda: images.row_count)
            assert images.row_count == 2
            images.move_cursor(row=images.get_row_index("acme/web"))
            await until(pilot, lambda: "cosign" in str(app.query_one("#packages-detail").render()))
            images.move_cursor(row=images.get_row_index("acme/api/backend"))
            await until(pilot, lambda: "NO IMAGE with this tag" in str(app.query_one("#packages-detail").render()))
            await pilot.press("7")
            await until(pilot, lambda: "…" not in str(app.query_one("#checks-detail").render()))
            assert "LICENSE differs from plugin-tpl" in str(app.query_one("#checks-detail").render())
            await pilot.press("9")
            await until(pilot, lambda: "demo one" in str(app.query_one("#demo-detail").render()))
            await pilot.press("o")
            await pilot.pause(0.3)
            log = [str(line.text) for line in app.query_one("#log").lines]
            assert any("demo key on acme/web" in line for line in log), log
            assert any("[dry-run] approve acme/api main" in line for line in log), log
            assert any("Prevent self-review" in line for line in log), log
            assert any("[dry-run] re-run the failed jobs of Release on v0.3.0" in line for line in log), log
            assert any("https://example.org/one" in line for line in log), log
            assert any("[dry-run] push tag v0.4.0 on main" in line for line in log), log
            assert any("[dry-run] update the branch of acme/web#7" in line for line in log), log
            assert any("[dry-run] comment on acme/web#7: thanks" in line for line in log), log
            assert any("[dry-run] branch bump/v0.5.0" in line for line in log), log
            assert any("[dry-run] create the draft release v0.3.1 (v0.3.1)" in line for line in log), log
            assert any("[dry-run] push the tag v0.3.1 on main of acme/plugin-b" in line for line in log), log

    asyncio.run(run())


def test_tui_menu_footer_settings_new(env, monkeypatch, tmp_path):
    for key in ("PATH", "FAKE_GH_LOG", "MASON_DEFAULTS", "MASON_OPEN", "MASON_PLUGINS"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from mason import config, core, tui

    core.DRY_RUN = True
    config._loaded.clear()
    cfg = config.load()

    async def run():
        app = tui.MasonTui(cfg, dry_run=True)
        async with app.run_test(size=(160, 45)) as pilot:
            table = app.query_one("#repos-table")
            await until(pilot, lambda: table.row_count)
            assert "runs" not in app.loaded and "security" not in app.loaded  # loaded on demand only
            table.move_cursor(row=table.get_row_index("acme/api"))
            await pilot.pause(0.2)
            assert app.check_action("diff", ()) is False and app.check_action("merge", ()) is False  # no PRs
            table.move_cursor(row=table.get_row_index("acme/web"))
            await pilot.pause(0.2)
            assert app.check_action("merge", ()) is True
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, tui.RowPage)  # Enter opens the row as a page
            labels = [str(o.prompt) for o in app.screen.query_one("OptionList").options]
            assert "Check it against the defaults" in labels and "Open in the browser (o)" in labels
            await until(pilot, lambda: "Open PRs:" in str(app.screen.query_one("#page-detail").render()))
            assert "Repos › acme/web" in str(app.screen.query_one("#page-title").render())
            await pilot.press("m")  # its key runs it from the page
            await pilot.pause(0.3)
            await pilot.press("y")
            await until(pilot, lambda: "auto-merge" in "".join(
                str(x.text) for x in app.screen.query_one("#page-log").lines))
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, tui.RowPage)
            await pilot.press("2")
            await pilot.pause(0.2)
            assert app.check_action("diff", ()) is True and app.check_action("approve", ()) is False
            await pilot.press("8")
            await until(pilot, lambda: "Defaults: acme/defaults" in str(app.query_one("#settings-detail").render()))
            await pilot.press("n")
            await pilot.pause(0.2)
            await pilot.press(*"acme/plugin-z", "enter")
            await pilot.pause(0.2)
            await pilot.press("enter")  # group: by match (plugins)
            await pilot.pause(0.2)
            await pilot.press("enter")  # private
            await pilot.pause(0.2)
            await pilot.press("y")
            await until(pilot, lambda: any("--template acme/plugin-tpl" in str(x.text)
                                           for x in app.query_one("#log").lines))
            await pilot.press("3")
            await until(pilot, lambda: "runs" in app.loaded)

    asyncio.run(run())
