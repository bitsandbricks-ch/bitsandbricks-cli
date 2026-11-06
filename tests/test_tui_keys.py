"""Keyboard only: Tab / Shift+Tab through the panels, arrows, and the F-keys for search, filter and sort."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")


async def until(pilot, check, seconds: float = 20) -> None:
    for _ in range(int(seconds * 4)):
        await pilot.pause(0.25)
        if check():
            return


def keys(row_keys) -> list[str]:
    return [k.value for k in row_keys]


def test_tui_keyboard(env, monkeypatch, tmp_path):
    for key in ("PATH", "FAKE_GH_LOG", "MASON_DEFAULTS", "MASON_OPEN"):
        monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("MASON_PLUGINS", "none")
    from textual.widgets import Button, DataTable, Footer, OptionList, RichLog, Tabs

    from mason import config, core, tui

    core.DRY_RUN = True
    config._loaded.clear()
    cfg = config.load()

    async def run():
        app = tui.MasonTui(cfg, dry_run=True)
        async with app.run_test(size=(160, 45)) as pilot:
            table = app.query_one("#repos-table", DataTable)
            await until(pilot, lambda: table.row_count)
            assert app.focused is table

            # Tab goes round: list, detail, log, tab bar, list again; Shift+Tab the other way.
            order = []
            for _ in range(4):
                await pilot.press("tab")
                order.append(app.focused)
            assert order[0].has_class("detail")
            assert isinstance(order[1], RichLog) and order[1].id == "log"
            assert isinstance(order[2], Tabs)
            assert order[3] is table
            await pilot.press("shift+tab")
            assert isinstance(app.focused, Tabs)

            # On the tab bar the arrows switch tabs, and the list of the new tab gets the focus.
            await pilot.press("right")
            await pilot.pause(0.2)
            assert app.tab() == "prs"
            assert app.focused is app.query_one("#prs-table")
            await pilot.press("left")
            await pilot.pause(0.2)
            assert app.tab() == "repos"

            # In the detail the arrows stay there (they scroll), they don't switch tabs.
            await pilot.press("tab")
            assert app.focused.has_class("detail")
            await pilot.press("right")
            await pilot.pause(0.1)
            assert app.tab() == "repos"
            await pilot.press("shift+tab")
            assert app.focused is table

            # The footer starts with the function keys, as htop's does.
            footer = app.query_one(Footer)
            await pilot.pause(0.2)
            shown = [k.key_display for k in footer.query("FooterKey")]
            assert shown[:6] == ["F1", "F3", "F4", "F5", "F6", "F9"], shown
            assert "F10" in shown

            # F3 (or /): only rows with the text stay; ← → move in the text, not between tabs.
            assert table.row_count == 6
            await pilot.press("f3")
            search = app.query_one("#search")
            assert app.focused is search
            await pilot.press(*"plugin")
            await pilot.pause(0.2)
            found = table.row_count
            assert 0 < found < 6
            assert all("plugin" in " ".join(str(c) for c in table.get_row(k)).lower() for k in keys(table.rows))
            await pilot.press("left", "left")
            await pilot.pause(0.1)
            assert app.tab() == "repos" and search.cursor_position == len("plugin") - 2
            assert f"{found} of 6 rows" in str(app.query_one("#view").render())
            await pilot.press("enter")  # back to the list, the search stays
            assert app.focused is table and table.row_count == found
            await pilot.press("2")  # each tab has its own search
            await pilot.pause(0.2)
            assert not search.has_class("-shown")
            await pilot.press("1")
            await pilot.pause(0.2)
            assert search.has_class("-shown") and search.value == "plugin"
            await pilot.press("slash", "escape")  # Esc clears it
            await pilot.pause(0.2)
            assert table.row_count == 6 and app.focused is table and not search.has_class("-shown")

            # F6: sort by a column (Tab alone goes through the choices), the same column again reverses it.
            await pilot.press("f6")
            await pilot.pause(0.2)
            assert isinstance(app.screen, tui.Pick)
            await pilot.press("tab", "enter")  # "as loaded" is first, then the repo column
            await pilot.pause(0.2)
            names = keys(table.rows)
            assert names == sorted(names, key=str.lower), names
            await pilot.press("greater_than_sign")
            await pilot.pause(0.2)
            await pilot.press("tab", "enter")
            await pilot.pause(0.2)
            assert keys(table.rows) == names[::-1]
            assert "F6 sort: repo ↓" in str(app.query_one("#view").render())

            # F4: pick a filter; v goes to the next one.
            await pilot.press("f4")
            await pilot.pause(0.2)
            labels = [str(o.prompt) for o in app.screen.query_one(OptionList).options]
            assert labels[0].startswith("all") and "with open PRs" in labels[1]
            await pilot.press("tab", "enter")
            await pilot.pause(0.2)
            with_prs = keys(table.rows)
            assert with_prs and all(
                (app.info[r].get("pullRequests") or {}).get("totalCount") for r in with_prs), with_prs
            assert len(with_prs) < 6
            await pilot.press("2")
            await pilot.pause(0.2)
            prs = app.query_one("#prs-table", DataTable)
            assert prs.row_count == 3
            await pilot.press("v", "v", "v")  # all open → auto-merge → recently merged → drafts
            await pilot.pause(0.2)
            assert all(app.prs[k]["isDraft"] for k in keys(prs.rows))
            assert "(drafts)" in str(app.query_one("#tabs").get_tab("prs").label)
            await pilot.press("v")
            await pilot.pause(0.2)
            assert prs.row_count == 3

            # F1 (or ?): every key, with the selected row's own keys; Esc closes it.
            await pilot.press("f1")
            await pilot.pause(0.2)
            assert isinstance(app.screen, tui.Help)
            text = str(app.screen.query_one("Static").render())
            assert "F4" in text and "Tab / Shift+Tab" in text and "On Pull requests" in text
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, tui.Help)

            # A dialog: Tab and the arrows move between Yes and No, Enter presses it.
            prs.move_cursor(row=prs.get_row_index("acme/web#7"))
            await pilot.press("m")
            await pilot.pause(0.2)
            assert isinstance(app.screen, tui.Confirm)
            first = app.focused
            await pilot.press("tab")
            assert isinstance(app.focused, Button) and app.focused is not first
            await pilot.press("left")
            assert app.focused is first
            await pilot.press("down")
            assert app.focused.id == "no"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, tui.Confirm)

            # F9 (like Enter) opens the row as a page; Tab goes through its panels.
            await pilot.press("f9")
            await pilot.pause(0.3)
            assert isinstance(app.screen, tui.RowPage)
            assert app.focused.id == "page-actions"
            await pilot.press("tab")
            assert app.focused.id == "page-detail-box"
            await pilot.press("tab")
            assert app.focused.id == "page-log"
            await pilot.press("f3")  # the list's keys don't reach under the page
            await pilot.pause(0.1)
            assert not app.query_one("#search").has_class("-shown")
            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, tui.RowPage)

    asyncio.run(run())


def test_sort_value():
    from rich.text import Text

    from mason import tui

    cells = ["3h ago", "just now", "2d ago", "15m ago"]
    assert sorted(cells, key=tui.sort_value) == ["just now", "15m ago", "3h ago", "2d ago"]
    assert sorted(["10", "9", "100"], key=tui.sort_value) == ["9", "10", "100"]
    assert tui.sort_value(Text("Beta")) < tui.sort_value("gamma")
    assert tui.blank("-") and tui.blank(Text(" ")) and not tui.blank("0")
