"""mason tui: one full-screen view of your repos across orgs.

Start it with `mason tui` (needs Textual: the [tui] extra or `pipx inject mason-cli textual`).

  Repos          group, latest release, PRs, CI per repo
  Pull requests  every open PR; the right side shows the selected one in depth
  Runs           runs waiting for your approval (or for someone else, and why), then the newest
                 run per repo; the right side lists its jobs and what failed, x re-runs failed jobs
  Releases       latest release per repo, missing assets, failed release runs and why
  Packages       container images: newest tags, whether the release has its image, image builds
  Security       open Dependabot, secret scanning and code scanning alerts
  Checks         the repos against the defaults (doctor), plus plugin checks
  ...            tabs from plugins

Nothing reloads on its own: the repo and PR list (one GraphQL query) loads at the
start, Runs, Releases, Packages and Security (API calls per repo) the first time you open their tab,
and r reloads the tab you are on. The header shows the API calls left.

Keys: Tab / Shift+Tab move between the tab bar, the list, its detail and the log · ← → or 1-9 switch
tabs · ↑ ↓ move · Enter or o open · m auto-merge · M merge now · A auto-merge all ready PRs · y mark ready
· d diff · a approve a waiting run · x re-run failed jobs · f fix drift and s sync files (Checks tab)
· n new repo · Enter: the row as a page (Esc back) · t theme · Every change asks first; --dry-run only shows it.

Function keys, as in htop and Midnight Commander (the letter in brackets does the same):
F1 help (?) · F2 every command (ctrl+p) · F3 search the list (/) · F4 filter (v: next filter)
· F5 refresh (r) · F6 sort (>) · F9 the row's menu (Enter) · F10 quit (q).
"""

from __future__ import annotations

import contextlib
import re
import time
import webbrowser
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual import work
from textual.actions import SkipAction
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)
from textual.widgets.data_table import CellDoesNotExist, RowDoesNotExist

from mason import config, core, plugins
from mason import defaults as D
from mason import packages as P
from mason import releases as REL
from mason import repos as R
from mason import versions as V
from mason.config import Config

BASE_TABS = [("repos", "Repos"), ("prs", "Pull requests"), ("runs", "Runs"), ("releases", "Releases"),
             ("packages", "Packages"), ("security", "Security"),
             ("checks", "Checks"), ("settings", "Settings")]
# Which tabs each key works on: the footer shows only the keys of the tab you are on.
TAB_ACTIONS = {
    "merge": {"repos", "prs"}, "merge_all": {"repos", "prs"}, "ready": {"prs"},
    "diff": {"prs"}, "approve": {"runs"}, "rerun": {"runs"}, "fix": {"checks"}, "sync": {"checks"},
    "bump": {"repos", "releases"}, "release": {"repos", "releases"},
    "new_release": {"repos", "releases"}, "new_tag": {"repos", "releases"},
    "update_branch": {"prs"}, "comment": {"prs"}, "close_pr": {"prs"},
    "logs": {"runs"}, "cancel_run": {"runs"}, "new_repo": {"repos"},
}
PAGE_TABS = {"repos", "prs", "runs", "releases", "packages", "security"}  # Enter opens the row as a page
LIST_TABS = PAGE_TABS  # tabs whose list can be searched (F3) and sorted (F6)
# F4: the filters of each list, the first one is what the tab shows by default.
FILTERS = {
    "repos": [("all", "all"), ("prs", "with open PRs"), ("failing", "CI failing")],
    "prs": [("all", "all open"), ("auto", "auto-merge"), ("merged", "recently merged"), ("drafts", "drafts")],
    "runs": [("all", "all"), ("waiting", "waiting"), ("failed", "failed"), ("running", "running")],
    "releases": [("all", "all"), ("problems", "with problems")],
    "packages": [("all", "all"), ("problems", "with problems")],
    "security": [("all", "all"), ("alerts", "with alerts")],
}
PR_FILTERS = FILTERS["prs"]
TAB_ACTIONS.update({"filter": set(FILTERS), "filter_next": set(FILTERS), "search": LIST_TABS, "sort": LIST_TABS})
VIEW_ACTIONS = {"filter", "filter_next", "search", "sort"}  # they change the list, not the row: not in keys_line
AGO = re.compile(r"^(\d+)([dhm]) ago$")
AGO_SECS = {"d": 86400, "h": 3600, "m": 60}


def plain(cell) -> str:
    return cell.plain if isinstance(cell, Text) else str(cell)


def sort_value(cell) -> tuple:
    """How F6 orders a cell: numbers and ages ("3h ago") by value, the rest as text."""
    s = plain(cell).strip()
    if s.isdigit():
        return (0, int(s), "")
    if s == "just now":
        return (0, 0, "")
    m = AGO.match(s)
    if m:
        return (0, int(m.group(1)) * AGO_SECS[m.group(2)], "")
    return (1, 0, s.lower())


def blank(cell) -> bool:
    return plain(cell).strip() in ("", "-")


HELP = """\
Moving around
  Tab / Shift+Tab   next / previous panel: tab bar, list, detail, search, log
  ← →               next / previous tab (on the list or the tab bar), 1-9 jump to a tab
  ↑ ↓ PgUp PgDn     move in the list, scroll the detail and the log
  Home End          first / last row
  Enter or F9       the selected row as a page, or its menu
  Esc               back from a page or a dialog

Lists (htop and Midnight Commander keys; the letter does the same)
  F1  ?             this help
  F2  ctrl+p        every command
  F3  /             search: only rows with this text stay; Esc clears it
  F4                pick a filter (v: the next filter)
  F5  r             refresh the tab you are on
  F6  >             sort by a column; the same column again reverses it
  F10 q             quit

Dialogs
  Tab / Shift+Tab   move between buttons, or through a list of choices
  y / n             yes / no, Enter picks, Esc cancels

Some terminals keep a function key for themselves (F1 help in GNOME Terminal, F10 its menu,
F11 full screen; on a Mac the F-keys need fn). The letter keys always work.
"""
SETTINGS = [("defaults", "Defaults repo"), ("orgs", "Orgs"), ("groups", "Groups"), ("plugins", "Plugins"),
            ("theme", "Theme"), ("file", "Config file")]

# mason: brick red and a warm sand on graphite.
BRICK, SAND, SKY = "#e2674a", "#f2c46d", "#6cb6d9"
SUCCESS, WARNING, DANGER = "#3fa76a", "#e0a43a", "#d9534f"
THEMES = [
    Theme(name="mason", dark=True, primary=SKY, secondary=BRICK, accent=SAND,
          foreground="#e9e7e4", background="#121314", surface="#1a1c1e", panel="#24272a",
          success=SUCCESS, warning=WARNING, error=DANGER,
          variables={"footer-key-foreground": SAND, "block-cursor-background": "#2f5d75",
                     "block-cursor-foreground": "#ffffff", "link-color": SKY, "border": "#2e3134"}),
    Theme(name="mason-light", dark=False, primary="#1f6f94", secondary=BRICK, accent="#b24a2f",
          foreground="#1d1f21", background="#f7f6f3", surface="#ffffff", panel="#ece9e3",
          success="#2a7d4f", warning=WARNING, error=DANGER,
          variables={"footer-key-foreground": "#b24a2f", "block-cursor-background": "#1f6f94",
                     "block-cursor-foreground": "#ffffff", "link-color": "#1f6f94", "border": "#e2ded6"}),
]

STATE_STYLE = {
    "success": SUCCESS, "passing": SUCCESS, "ok": SUCCESS, "ready": SUCCESS,
    "failure": DANGER, "failing": DANGER, "error": DANGER, "conflicts": DANGER,
    "pending": WARNING, "running": WARNING, "waiting": WARNING, "in_progress": WARNING, "auto-merge": SKY,
}
CACHE = "60s"  # gh answers the same GET from its cache for this long
URL = re.compile(r"https?://[^\s)\]>,'\"]+")


def styled(value: str) -> Text:
    for word, style in STATE_STYLE.items():
        if value.lower().startswith(word):
            return Text(value, style=style)
    return Text(value)


def link_label(url: str) -> str:
    for prefix in (f"{core.WEB}/", "https://"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return url


def linkify(text: str, color: str = SKY) -> Text:
    """Text with every URL as a short, clickable label: one unbroken word, so it never wraps apart."""
    out = Text()
    pos = 0
    for m in URL.finditer(text):
        out.append(text[pos:m.start()])
        url = m.group(0).rstrip(".")
        out.append(link_label(url), style=Style(color=color, underline=True,
                                                meta={"@click": f"app.open_link({url!r})"}))
        pos = m.start() + len(url)
    out.append(text[pos:])
    return out


class Confirm(ModalScreen[bool]):
    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Vertical { width: 80; height: auto; border: thick $accent; background: $surface; padding: 1 2; }
    Confirm Horizontal { height: auto; margin-top: 1; }
    Confirm Button { margin-right: 2; }
    """
    # Tab / Shift+Tab (and ← → ↑ ↓) move between the buttons, Enter presses the one that has focus.
    BINDINGS: ClassVar = [Binding("y", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No"),
                          Binding("down", "app.focus_next", show=False),
                          Binding("up", "app.focus_previous", show=False)]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self.question))
            with Horizontal():
                yield Button("Yes (y)", variant="warning", id="yes")
                yield Button("No (n)", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class Ask(ModalScreen[str]):
    """One line of input; Enter answers, Esc cancels."""

    DEFAULT_CSS = """
    Ask { align: center middle; }
    Ask > Vertical { width: 80; height: auto; border: thick $accent; background: $surface; padding: 1 2; }
    Ask Input { margin-top: 1; }
    """
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, question: str, password: bool = False, value: str = "") -> None:
        super().__init__()
        self.question, self.password, self.value = question, password, value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(Text(self.question))
            yield Input(value=self.value, password=self.password)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss("")


class Pick(ModalScreen[str]):
    """Choose one of a few options; Enter picks, Esc cancels."""

    DEFAULT_CSS = """
    Pick { align: center middle; }
    Pick > Vertical { width: 80; height: auto; border: thick $accent; background: $surface; padding: 1 2; }
    Pick OptionList { height: auto; max-height: 12; margin-top: 1; }
    """
    # The list is the only thing to focus here, so Tab / Shift+Tab go through its choices (like a shell's
    # completion menu): Tab alone is enough to pick one.
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel"),
                          Binding("tab", "move(1)", show=False, priority=True),
                          Binding("shift+tab", "move(-1)", show=False, priority=True)]

    def __init__(self, question: str, options: list[tuple[str, str]]) -> None:
        super().__init__()
        self.question, self.options = question, options

    def compose(self) -> ComposeResult:
        from textual.widgets.option_list import Option
        with Vertical():
            yield Label(Text(self.question))
            yield OptionList(*[Option(label, id=key) for key, label in self.options])

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id or "")

    def action_move(self, step: int) -> None:
        options = self.query_one(OptionList)
        options.action_cursor_down() if step > 0 else options.action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss("")


class Help(ModalScreen[None]):
    """F1 or ?: every key on one page. Esc, F1, ? or q closes it."""

    DEFAULT_CSS = """
    Help { align: center middle; }
    Help > VerticalScroll { width: 104; max-width: 100%; height: auto; max-height: 90%;
                            border: thick $accent; background: $surface; padding: 1 2; }
    """
    BINDINGS: ClassVar = [Binding("escape,f1,question_mark,q", "close", "Close")]

    def __init__(self, extra: str = "") -> None:
        super().__init__()
        self.extra = extra

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(HELP + self.extra, markup=False)

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()

    def action_close(self) -> None:
        self.dismiss(None)


class Search(Input):
    """The search line under the tabs (F3 or /): the list keeps the rows with this text as you type.
    Enter or ↓ goes back to the list and keeps it, Esc clears it."""

    BINDINGS: ClassVar = [Binding("escape", "clear_search", show=False), Binding("down", "done", show=False)]

    def action_clear_search(self) -> None:
        self.value = ""
        self.app.end_search(clear=True)

    def action_done(self) -> None:
        self.app.end_search()


KEY_HINT = re.compile(r"\(([^()\s])\)$")  # "Merge now (M)" -> M


class RowPage(ModalScreen[None]):
    """One row as a full page: what you can do on the left (Enter or its key), its detail on the right,
    what the actions printed at the bottom. Esc goes back to the list."""

    DEFAULT_CSS = """
    RowPage { background: $background; }
    RowPage #page-title { height: 1; padding: 0 1; background: $panel; color: $accent; text-style: bold; }
    RowPage #page-body { height: 1fr; }
    RowPage #page-actions { width: 46; height: 1fr; border-right: solid $primary; background: $surface; }
    RowPage #page-actions:focus { border-left: tall $accent; }
    RowPage #page-detail-box { width: 1fr; height: 1fr; padding: 0 1; overflow-x: auto; }
    RowPage #page-detail-box:focus { border-left: tall $accent; padding-left: 0; }
    RowPage #page-detail { width: auto; }
    RowPage #page-log { height: 8; border-top: solid $primary; background: $surface; padding: 0 1; }
    RowPage #page-log:focus { border-top: tall $accent; }
    RowPage #page-help { height: 1; padding: 0 1; background: $panel; color: $text-muted; }
    """
    BINDINGS: ClassVar = [Binding("escape,backspace", "back", "Back"), Binding("f5,r", "reload", "Refresh")]

    def __init__(self, tab: str, key: str, title: str) -> None:
        super().__init__()
        self.tab, self.key, self.title_text = tab, key, title
        self.items: list[tuple[str, str, object]] = []

    def compose(self) -> ComposeResult:
        yield Static(self.title_text, id="page-title", markup=False)
        with Horizontal(id="page-body"):
            yield OptionList(id="page-actions")
            with VerticalScroll(id="page-detail-box"):  # Tab: the actions, the detail, the log
                yield Static("Loading …", id="page-detail", markup=False)
        yield RichLog(id="page-log", wrap=False, markup=False, min_width=40)
        yield Static("↑ ↓ pick · Enter or the key in ( ) runs it · Tab next panel · r refresh · Esc back",
                     id="page-help",
                     markup=False)

    def on_mount(self) -> None:
        self.fill_items()
        self.app.update_detail()  # shows itself here too (MasonTui.show)
        if self.tab == "releases":
            self.app.load_release_page(self.key)  # every recent release and the whole notes
        self.query_one("#page-actions", OptionList).focus()

    def fill_items(self) -> None:
        from textual.widgets.option_list import Option
        self.items = [i for i in self.app.menu_items() if i[0] != "page"]
        actions = self.query_one("#page-actions", OptionList)
        highlighted = actions.highlighted
        actions.clear_options()
        actions.add_options([Option(label, id=k) for k, label, _ in self.items] or [Option("nothing to do", id="")])
        actions.highlighted = highlighted if highlighted is not None and highlighted < len(self.items) else 0

    def set_detail(self, text: Text) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#page-detail", Static).update(text)

    def write(self, line: Text) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#page-log", RichLog).write(line)

    def run_item(self, k: str) -> None:
        fn = next((f for i, _, f in self.items if i == k), None)
        if fn is None:
            return
        fn()
        if self.app.tab() != self.tab:  # the action went to another tab (alerts, the repo of an image)
            self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.run_item(event.option.id)

    def on_key(self, event) -> None:
        for k, label, _ in self.items:
            m = KEY_HINT.search(label)
            if m and event.character == m.group(1):
                event.stop()
                self.run_item(k)
                return

    def action_back(self) -> None:
        self.dismiss(None)

    def action_reload(self) -> None:
        self.app.action_refresh()


class MasonTui(App):
    TITLE = "mason"
    CSS = """
    Screen { background: $background; }
    Header { background: $panel; color: $foreground; }
    #tabs { height: 1fr; }
    #tabs ContentSwitcher { height: 1fr; }
    Tabs .underline--bar { color: $accent; background: $panel; }
    Tab.-active { color: $accent; text-style: bold; }
    TabPane { height: 1fr; padding: 0; }
    .split { height: 1fr; }
    .split DataTable { width: 3fr; height: 1fr; background: $surface; }
    .split DataTable:focus { border-left: tall $accent; }
    .detail:focus { border-left: tall $accent; }
    #log:focus { border-top: tall $accent; }
    Tabs:focus .-active { background: $block-cursor-background; color: $block-cursor-foreground; }
    #view { height: 1; padding: 0 1; background: $panel; color: $text-muted; }
    #search { display: none; height: 1; border: none; padding: 0 1; background: $surface; }
    #search.-shown { display: block; }
    #search:focus { border: none; background: $panel; }
    .split DataTable.narrow { width: 24; }
    DataTable > .datatable--header { background: $panel; color: $primary; text-style: bold; }
    .detail { width: 2fr; height: 1fr; border-left: solid $primary; padding: 0 1;
              overflow-x: auto; overflow-y: auto; background: $background; }
    .detail Static { width: auto; }
    #log { height: 8; border-top: solid $primary; background: $surface; padding: 0 1; scrollbar-size-horizontal: 1; }
    * { scrollbar-background: $surface; scrollbar-background-hover: $surface;
        scrollbar-color: $primary 40%; scrollbar-color-hover: $primary 70%; scrollbar-color-active: $primary; }
    """
    BINDINGS: ClassVar = [
        Binding("left", "next_tab(-1)", "Prev tab", show=False, priority=True),
        Binding("right", "next_tab(1)", "Next tab", show=False, priority=True),
        *(Binding(str(i), f"tab_number({i})", show=False) for i in range(1, 10)),
        # The function keys first, so the footer starts with them, as htop's does. Each also has a letter
        # (the footer shows the F-key): some terminals keep F1 and F10 for themselves.
        Binding("f1,question_mark", "help", "Help", key_display="F1"),
        Binding("f2", "command_palette", "Commands", key_display="F2", show=False),
        Binding("f3,slash", "search", "Search", key_display="F3"),
        Binding("f4", "filter", "Filter", key_display="F4"),
        Binding("f5,r", "refresh", "Refresh", key_display="F5"),
        Binding("f6,greater_than_sign", "sort", "Sort", key_display="F6"),
        Binding("f9", "menu", "Menu", key_display="F9"),
        Binding("f10,q", "quit", "Quit", key_display="F10"),
        Binding("v", "filter_next", "Next filter", show=False),
        Binding("o", "open", "Open"),
        Binding("t", "toggle_theme", "Theme", show=False),
        Binding("m", "merge(False)", "Auto-merge"),
        Binding("M", "merge(True)", "Merge now"),
        Binding("A", "merge_all", "Auto-merge all"),
        Binding("y", "ready", "Ready"),
        Binding("d", "diff", "Diff"),
        Binding("a", "approve", "Approve"),
        Binding("x", "rerun", "Re-run failed"),
        Binding("l", "logs", "Failed log"),
        Binding("C", "cancel_run", "Cancel run"),
        Binding("u", "update_branch", "Update branch"),
        Binding("c", "comment", "Comment"),
        Binding("C", "close_pr", "Close PR"),
        Binding("b", "bump", "Bump version"),
        Binding("R", "release", "Release"),
        Binding("N", "new_release", "New release"),
        Binding("T", "new_tag", "New tag"),
        Binding("f", "fix", "Fix drift"),
        Binding("s", "sync", "Sync files"),
        Binding("n", "new_repo", "New repo"),
    ]

    def __init__(self, cfg: Config, dry_run: bool = False, group: str | None = None) -> None:
        super().__init__()
        self.cfg, self.dry_run, self.group = cfg, dry_run, group
        self.base_title = "dry run: nothing changes" if dry_run else (", ".join(cfg.orgs) or "no orgs")
        self.sub_title = self.base_title
        self.repos: list[str] = []
        self.prs: dict[str, dict] = {}       # "owner/repo#12" -> PR node
        self.runs: dict[str, tuple] = {}     # key -> (repo, run, envs)
        self.detail_cache: dict[str, str] = {}
        self.info: dict[str, dict] = {}
        self.last_summary = ""
        self.last_waiting = 0
        self.filters: dict[str, str] = {}               # tab -> F4 filter (FILTERS); missing: the first one
        self.searches: dict[str, str] = {}              # tab -> F3 search text
        self.sorts: dict[str, tuple[int, bool]] = {}    # tab -> F6 (column, reversed)
        self.rows: dict[str, list[tuple[str, list]]] = {}  # tab -> every row, before filter, search and sort
        self.heads: dict[str, list[str]] = {}
        self.alerts: dict[str, tuple[list[dict], list[str]]] = {}
        self.releases: dict[str, dict] = {}  # repo -> R.release_info
        self.packages: dict[str, dict] = {}  # owner/image -> P.package_infos entry
        self.versions: dict[str, dict | str] = {}  # repo -> V.state, or an error
        self.last_alerts = 0
        self.plugin_tabs = {t["id"]: t for t in plugins.hook("tui_tabs")}
        self.tabs = BASE_TABS + [(t["id"], t["title"]) for t in self.plugin_tabs.values()]
        self.checks = [("doctor", "Defaults (doctor)", None)] + plugins.hook("tui_checks")
        self.plugin_keys = plugins.hook("tui_keys")
        self.themes = THEMES + plugins.hook("tui_themes")
        self.loaded: dict[str, float] = {}  # tab -> when its data was loaded
        self.force = False
        self.rate = ""

    # ── helpers for plugins ───────────────────────────────────────────────

    def label(self, repo: str) -> str:
        """owner/name, or just name when there is one org."""
        return core.short(repo) if len(self.cfg.orgs) <= 1 and repo.split("/")[0] in self.cfg.orgs else repo

    def selected_repo(self) -> str | None:
        """The repo of the selected row in Repos, Pull requests, Runs or Security."""
        tab, key = self.tab(), self.current_key()
        if not key:
            return None
        if tab == "prs":
            return self.prs[key]["repo"]
        if tab == "runs":
            return self.runs[key][0]
        if tab == "packages":
            return self.packages[key]["repo"]
        return key if tab in ("repos", "security", "releases") else None

    def ask(self, question: str, then, password: bool = False) -> None:
        self.push_screen(Ask(question, password), then)

    def pick(self, question: str, options: list[tuple[str, str]], then) -> None:
        self.push_screen(Pick(question, options), then)

    # ── layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="tabs"):
            for tab_id, title in self.tabs:
                narrow = tab_id in ("checks", "settings") or tab_id in self.plugin_tabs
                with TabPane(title, id=tab_id), Horizontal(classes="split"):
                    yield DataTable(id=f"{tab_id}-table", cursor_type="row", zebra_stripes=True,
                                    classes="narrow" if narrow else "")
                    with VerticalScroll(classes="detail"):  # focusable: ↑ ↓ scroll it after a Tab
                        yield Static(id=f"{tab_id}-detail", markup=False)
        yield Search(placeholder="search: only rows with this text stay · Enter keeps it · Esc clears", id="search")
        yield Static(id="view", markup=False)
        yield RichLog(id="log", wrap=False, markup=False, min_width=40)
        yield Footer()

    def on_mount(self) -> None:
        for theme in self.themes:
            self.register_theme(theme)
        self.theme = self.themes[0].name
        heads = {"repos": R.status_head(), "prs": ["repo", "#", "title", "state", "checks"],
                 "runs": ["repo", "state", "workflow", "ref", "when"],
                 "releases": ["repo", "release", "when", "assets", "release run"],
                 "packages": ["image", "newest", "updated", "release", "image build"],
                 "security": ["repo", "alerts", "worst", "kinds"],
                 "checks": ["check"], "settings": ["setting"], **{t: ["view"] for t in self.plugin_tabs}}
        self.heads = heads
        for tab_id, head in heads.items():
            self.query_one(f"#{tab_id}-table", DataTable).add_columns(*head)
        checks = self.query_one("#checks-table", DataTable)
        settings = self.query_one("#settings-table", DataTable)
        for key, label in SETTINGS:
            settings.add_row(label, key=key)
        for key, label, _ in self.checks:
            checks.add_row(label, key=key)
        for tab_id, t in self.plugin_tabs.items():
            table = self.query_one(f"#{tab_id}-table", DataTable)
            for key, label in t.get("rows") or []:
                table.add_row(label, key=key)
        for i, (key, description, *_) in enumerate(self.plugin_keys):
            if self.key_taken(key, self.key_tabs(i)):
                self.log_line(f"plugin key {key} ({description}) left out: mason uses {key} on those tabs")
                continue
            self.bind(key, f"plugin_key({i})", description=description)
        for err in plugins.errors:
            self.log_line(f"plugin broken: {err}")
        self.log_line(f"Loading … Tab moves between panels, ← → or 1-{min(len(self.tabs), 9)} switch tabs, "
                      "↑ ↓ move, Enter opens, F3 searches, F4 filters, F6 sorts, F1 lists every key, F10 quits")
        self.focus_table()
        self.load_github()

    def log_line(self, text: str) -> None:
        line = Text(time.strftime("%H:%M:%S "), style="dim")
        line.append_text(linkify(text.strip()))
        with contextlib.suppress(NoMatches):  # the app is closing
            self.query_one("#log", RichLog).write(line)
        for screen in self.screen_stack:
            if isinstance(screen, RowPage):
                screen.write(line)

    def focus_table(self, tab_id: str | None = None) -> None:
        with contextlib.suppress(Exception):
            self.query_one(f"#{tab_id or self.tab()}-table", DataTable).focus()

    def action_tab(self, tab_id: str) -> None:
        self.query_one("#tabs", TabbedContent).active = tab_id
        self.focus_table(tab_id)

    def action_tab_number(self, n: int) -> None:
        if n <= len(self.tabs):
            self.action_tab(self.tabs[n - 1][0])

    def highlight(self) -> str:
        return "#b24a2f" if self.theme == "mason-light" else SAND

    def action_next_tab(self, step: int) -> None:
        """← →: the next tab from the list or the tab bar, the next button in a dialog. Anywhere else
        (a search or an answer you type, the detail) the arrows stay with what has focus."""
        focused = self.focused
        if isinstance(self.screen, ModalScreen):
            if not isinstance(focused, Button):
                raise SkipAction()
            self.screen.focus_next() if step > 0 else self.screen.focus_previous()
            return
        if focused is not None and not isinstance(focused, (DataTable, Tabs)):
            raise SkipAction()
        order = [t for t, _ in self.tabs]
        self.action_tab(order[(order.index(self.tab()) + step) % len(order)])

    def action_toggle_theme(self) -> None:
        names = [t.name for t in self.themes]
        self.theme = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]

    def action_open_link(self, url: str) -> None:
        mode = core.open_mode()
        if mode == "browser":
            self.log_line(f"open {url}")
            webbrowser.open(url)
            return
        line = Text(time.strftime("%H:%M:%S "), style="dim")
        if mode == "copy":
            self.copy_to_clipboard(url)  # OSC 52: your own terminal's clipboard, also over SSH
            line.append("copied ")
        line.append(url, style=Style(underline=True, link=url))
        with contextlib.suppress(NoMatches):
            self.query_one("#log", RichLog).write(line)

    def key_tabs(self, i: int) -> set[str]:
        """Tabs a plugin key works on: its 4th item, or Repos and Pull requests."""
        k = self.plugin_keys[i]
        return set(k[3]) if len(k) > 3 and k[3] else {"repos", "prs"}

    def key_taken(self, key: str, tabs: set[str]) -> bool:
        """Whether a mason key already works on one of these tabs (a key may serve mason and a plugin on
        different tabs)."""
        for b in self.BINDINGS:
            if key in (k.strip() for k in b.key.split(",")):
                action = b.action.split("(")[0]
                if action not in TAB_ACTIONS or TAB_ACTIONS[action] & tabs:
                    return True
        return False

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Hide the keys that do nothing on this tab, so the footer shows what you can do here."""
        tab = self.tab()
        if isinstance(self.screen, ModalScreen) and action in VIEW_ACTIONS | {"menu"}:
            return False  # they work on the list, which a page or a dialog covers
        if not tab or isinstance(self.screen, ModalScreen):
            return True
        if action in TAB_ACTIONS:
            return tab in TAB_ACTIONS[action] and self.row_allows(action, tab)
        if action == "plugin_key" and parameters:
            return tab in self.key_tabs(int(parameters[0]))
        return True

    def row_allows(self, action: str, tab: str) -> bool:
        """Whether a key does something for the selected row, so the footer leaves out the rest
        (a key that explains why it can't, like approving your own release, stays)."""
        key = self.current_key(tab)
        try:
            if tab == "repos" and action == "merge":
                return bool(((self.info.get(key) or {}).get("pullRequests") or {}).get("totalCount"))
            if tab == "prs" and action in ("merge", "update_branch", "comment", "close_pr", "ready"):
                p = self.prs[key]
                return not p.get("merged") and (action != "ready" or p["isDraft"])
            if tab == "runs" and action in ("approve", "rerun", "logs", "cancel_run"):
                _, run, envs = self.runs[key]
                state = run.get("conclusion") or run.get("status")
                return {"approve": bool(envs), "rerun": state in R.FAILED, "logs": state in R.FAILED,
                        "cancel_run": run.get("status") in R.ACTIVE}[action]
            if action in ("bump", "release"):
                return bool(key) and bool(V.source(self.cfg, key))
        except (KeyError, TypeError):
            return False
        return True

    def keys_line(self, tab: str) -> str:
        """The keys that work on this tab's selected row, for the top of its detail."""
        keys = []
        for b in self.BINDINGS:
            action = b.action.split("(")[0]
            if action in VIEW_ACTIONS:
                continue
            if action in TAB_ACTIONS and tab in TAB_ACTIONS[action] and self.row_allows(action, tab):
                keys.append(f"{b.key} {b.description.lower()}")
        keys += [f"{k[0]} {k[1].lower()}" for i, k in enumerate(self.plugin_keys)
                 if tab in self.key_tabs(i) and not self.key_taken(k[0], self.key_tabs(i))]
        out, line = [], ""
        for k in keys + ["enter more", "o open"]:
            if line and len(line) + len(k) > 56:
                out.append(line)
                line = ""
            line = f"{line}  ·  {k}" if line else k
        return "\n".join([*out, line])

    def action_plugin_key(self, i: int) -> None:
        try:
            self.plugin_keys[i][2](self)
        except Exception as err:
            self.log_line(f"plugin key failed: {err}")

    def tab(self) -> str:
        try:
            return self.query_one("#tabs", TabbedContent).active
        except NoMatches:  # the app is closing
            return ""

    def current_key(self, tab_id: str | None = None) -> str | None:
        tab_id = tab_id or self.tab()
        if not tab_id:
            return None
        try:
            table = self.query_one(f"#{tab_id}-table", DataTable)
        except NoMatches:
            return None
        if table.row_count == 0:
            return None
        try:
            return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        except (KeyError, IndexError, ValueError, CellDoesNotExist):
            return None

    def show(self, tab_id: str, text: str) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, RowPage) and screen.tab == tab_id and screen.key == self.current_key(tab_id):
                page = self.detail_cache.get(f"relpage:{screen.key}") if tab_id == "releases" else None
                screen.set_detail(linkify(page or text))
        with contextlib.suppress(NoMatches):
            body = linkify(text)
            if tab_id not in ("settings", "checks") and tab_id not in self.plugin_tabs:
                body = Text.assemble((self.keys_line(tab_id) + "\n\n", Style(color=SAND, dim=True)), body)
            self.query_one(f"#{tab_id}-detail", Static).update(body)

    # ── loading ───────────────────────────────────────────────────────────

    @work(thread=True, exclusive=True, group="github")
    def load_github(self) -> None:
        R._listing.clear()
        try:
            self.repos = R.pick_repos(self.cfg, [], self.group)[0]
            rows = R.status_rows(self.cfg, self.repos)
            info = R.nodes(self.repos)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            self.call_from_thread(self.log_line, f"GitHub: {err}")
            return
        prs = {}
        for repo in self.repos:
            for p in ((info.get(repo) or {}).get("pullRequests") or {}).get("nodes") or []:
                prs[f"{repo}#{p['number']}"] = dict(p, repo=repo)
        self.call_from_thread(self.fill_github, rows, prs, info)
        self.call_from_thread(self.load_tab_data)
        self.check_rate()

    def fill_github(self, rows: list[list[str]], prs: dict[str, dict], info: dict) -> None:
        with contextlib.suppress(NoMatches):
            self._fill_github(rows, prs, info)

    def _fill_github(self, rows: list[list[str]], prs: dict[str, dict], info: dict) -> None:
        self.prs, self.info, self.detail_cache = prs, info, {}
        out = []
        for repo, row in zip(self.repos, rows):
            cells = [Text(c) for c in row]
            cells[0] = Text(self.label(repo))
            cells[4] = styled(row[4])
            out.append((repo, cells))
        self.put_rows("repos", out)
        for repo in self.repos:
            for p in ((info.get(repo) or {}).get("merged") or {}).get("nodes") or []:
                self.prs[f"{repo}#{p['number']}"] = dict(p, repo=repo, merged=True, isDraft=False)
        self.fill_prs()
        summary = f"{len(self.repos)} repos, {len(prs)} open PRs"
        auto = sum(1 for p in prs.values() if p.get("autoMergeRequest"))
        if auto:
            summary += f", {auto} auto-merging (v filters the PR list)"
        if summary != self.last_summary:
            self.last_summary = summary
            self.log_line(summary)
        self.update_detail()

    def fill_prs(self) -> None:
        rows = []
        for key, p in self.prs.items():
            if p.get("merged"):
                state, checks, sort = Text(f"merged {core.ago(p.get('mergedAt'))}", style=SUCCESS), Text(""), 3
            else:
                commit = (p["commits"]["nodes"] or [{}])[0].get("commit") or {}
                word = R.checks_word(commit.get("statusCheckRollup"))
                checks, sort = styled(word), 1
                if p.get("mergeable") == "CONFLICTING":
                    state = styled("conflicts")
                elif p.get("autoMergeRequest"):
                    state = Text(f"auto-merge · {'blocked' if word == 'failing' else 'waits for checks'}",
                                 style=DANGER if word == "failing" else SKY)
                    sort = 0
                else:
                    state = styled("draft" if p["isDraft"] else "ready")
                    sort = 2 if p["isDraft"] else 1
            rows.append((sort, key, [self.label(p["repo"]), str(p["number"]), Text(p["title"][:60]),
                                     state, checks]))
        self.put_rows("prs", [(key, cells) for _, key, cells in sorted(rows, key=lambda r: r[0])])
        self.label_prs()

    def label_prs(self) -> None:
        f = self.filter_of("prs")
        auto = sum(1 for p in self.prs.values() if p.get("autoMergeRequest"))
        with contextlib.suppress(NoMatches):
            self.query_one("#tabs", TabbedContent).get_tab("prs").label = (
                "Pull requests" + (f" ({dict(PR_FILTERS)[f]})" if f != "all" else "")
                + (f" · {auto} auto-merging" if auto else ""))

    @work(thread=True, exclusive=True, group="security")
    def load_security(self) -> None:
        self.loaded["security"] = time.time()
        try:
            found = dict(zip(self.repos, core.pmap(R.repo_alerts, self.repos)))
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            self.call_from_thread(self.log_line, f"Security: {err}")
            return
        self.call_from_thread(self.fill_security, found)

    def fill_security(self, found: dict) -> None:
        with contextlib.suppress(NoMatches):
            self.alerts = found
            out = []
            rank = R.SEVERITY_ORDER
            rows = sorted(found.items(), key=lambda kv: (rank.get(kv[1][0][0]["severity"], 5) if kv[1][0] else 9,
                                                          -len(kv[1][0]), kv[0]))
            for repo, (alerts, unread) in rows:
                worst = alerts[0]["severity"] if alerts else ("?" if unread else "-")
                style = DANGER if worst in ("critical", "high", "secret") else WARNING if alerts else ""
                kinds = ", ".join(sorted({a["kind"] for a in alerts})) or ("can't read all" if unread else "")
                out.append((repo, [self.label(repo), Text(str(len(alerts)), style=style), Text(worst, style=style),
                                   kinds]))
            self.put_rows("security", out)
            total = sum(len(a) for a, _ in found.values())
            self.query_one("#tabs", TabbedContent).get_tab("security").label = (
                f"Security ({total})" if total else "Security")
            if total and total != self.last_alerts:
                self.log_line(f"{total} open security alert(s): Security tab (6)")
            self.last_alerts = total
            self.update_detail()

    @work(thread=True, exclusive=True, group="releases")
    def load_releases(self) -> None:
        self.loaded["releases"] = time.time()
        try:
            found = R.release_infos(self.cfg, self.repos)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        self.call_from_thread(self.fill_releases, found)

    def fill_releases(self, found: list[dict]) -> None:
        with contextlib.suppress(NoMatches):
            self.releases = {i["repo"]: i for i in found}
            out = []
            bad = 0
            for i in sorted(found, key=lambda i: (not R.release_problems(i), i["repo"])):
                tag, when, files, run = R.release_row(i)
                problems = R.release_problems(i)
                bad += bool(problems)
                last = i["runs"][0] if i["runs"] else {}
                out.append((i["repo"], [self.label(i["repo"]), tag, when,
                                        Text(files, style=DANGER if i["missing"] else ""),
                                        Text(run, style=STATE_STYLE.get(R.run_state(last), "") if last else "")]))
            self.put_rows("releases", out)
            self.query_one("#tabs", TabbedContent).get_tab("releases").label = (
                f"Releases ({bad})" if bad else "Releases")
            if bad:
                self.log_line(f"{bad} repo(s) with release problems: Releases tab (4)")
            self.update_detail()

    @work(thread=True, exclusive=True, group="packages")
    def load_packages(self) -> None:
        self.loaded["packages"] = time.time()
        try:
            found, err = P.package_infos(self.cfg, self.repos, self.info)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        self.call_from_thread(self.fill_packages, found, err)

    def fill_packages(self, found: list[dict], err: str) -> None:
        with contextlib.suppress(NoMatches):
            self.packages = {f"{i['owner']}/{i['name']}": i for i in found}
            out = []
            bad = 0
            for key, i in sorted(self.packages.items(), key=lambda kv: (not P.problems(kv[1]), kv[0])):
                newest, updated, release, run = P.row(i)
                bad += bool(P.problems(i))
                last = i["runs"][0] if i["runs"] else {}
                out.append((key, [key, newest, updated, Text(release, style=DANGER if "missing" in release else ""),
                                  Text(run, style=STATE_STYLE.get(R.run_state(last), "") if last else "")]))
            self.put_rows("packages", out)
            self.query_one("#tabs", TabbedContent).get_tab("packages").label = (
                f"Packages ({bad})" if bad else "Packages")
            if err:
                self.log_line(f"Packages: {err}")
                self.show("packages", err)
            elif not found:
                self.show("packages", "No container images in your orgs.")
            elif bad:
                self.log_line(f"{bad} image(s) with problems: Packages tab (5)")
            self.update_detail()

    @work(thread=True, group="detail")
    def load_image_jobs(self, key: str, repo: str, run_id: int) -> None:
        try:
            jobs = R.failed_jobs(repo, run_id)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            jobs = [f"error: {err}"]
        text = P.text(self.packages[key]) + "\n\nWhat failed in the last image build:\n" + "\n".join(
            f"  {j}" for j in jobs or ["no failed jobs listed"])
        self.detail_cache[f"packages:{key}"] = text
        self.call_from_thread(self.show_if_current, "packages", key, text)

    def release_url(self, repo: str) -> str:
        rel = (self.releases.get(repo) or {}).get("release") or {}
        return rel.get("html_url") or f"{core.WEB}/{repo}/releases"

    @work(thread=True, group="detail")
    def load_release_jobs(self, repo: str, run_id: int) -> None:
        """The failed jobs and steps of a repo's last release run, under its release detail."""
        try:
            jobs = R.failed_jobs(repo, run_id)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            jobs = [f"error: {err}"]
        text = R.release_text(self.releases[repo]) + "\n\nWhat failed in the last run:\n" + "\n".join(
            f"  {j}" for j in jobs or ["no failed jobs listed"])
        self.detail_cache[f"releases:{repo}"] = text
        self.call_from_thread(self.refresh_version, repo)  # shows it with the version on top

    def security_text(self, repo: str) -> str:
        alerts, unread = self.alerts.get(repo, ([], []))
        lines = [repo, f"{core.WEB}/{repo}/security", ""]
        for a in alerts:
            lines.append(f"{a['severity']:<9} {a['kind']:<16} {a['title']}")
            if a["where"]:
                lines.append(f"{'':<26} {a['where']}")
            lines.append(f"{'':<26} {a['url']}")
        if not alerts:
            lines.append("No open alerts.")
        if unread:
            lines += ["", "Couldn't read:"] + [f"  {u}" for u in unread]
        return "\n".join(lines)

    # ── the list: F3 search, F4 filter, F6 sort ──────────────────────────

    def filter_of(self, tab: str) -> str:
        return self.filters.get(tab) or FILTERS[tab][0][0]

    def keeps(self, tab: str, key: str) -> bool:
        """Whether the tab's F4 filter keeps this row."""
        f = self.filter_of(tab) if tab in FILTERS else "all"
        try:
            if tab == "prs":
                p = self.prs[key]
                return bool({"all": not p.get("merged"), "auto": p.get("autoMergeRequest") or p.get("merged"),
                             "merged": p.get("merged"), "drafts": p.get("isDraft")}[f])
            if f == "all":
                return True
            if tab == "repos":
                node = self.info.get(key) or {}
                if f == "prs":
                    return bool((node.get("pullRequests") or {}).get("totalCount"))
                rollup = ((node.get("defaultBranchRef") or {}).get("target") or {}).get("statusCheckRollup") or {}
                return (rollup.get("state") or "") in ("FAILURE", "ERROR")
            if tab == "runs":
                _, run, envs = self.runs[key]
                return {"waiting": bool(envs) or run.get("status") == "waiting",
                        "failed": (run.get("conclusion") or "") in R.FAILED,
                        "running": run.get("status") in R.ACTIVE}[f]
            if tab == "releases":
                return bool(R.release_problems(self.releases[key]))
            if tab == "packages":
                return bool(P.problems(self.packages[key]))
            if tab == "security":
                return bool(self.alerts.get(key, ([], []))[0])
        except (KeyError, TypeError):
            return True
        return True

    def put_rows(self, tab: str, rows: list[tuple[str, list]]) -> None:
        """A list's rows, in the app's own order; what it shows goes through the filter, search and sort."""
        self.rows[tab] = rows
        self.apply_view(tab)

    def apply_view(self, tab: str) -> None:
        table = self.query_one(f"#{tab}-table", DataTable)
        keep = self.current_key(tab)
        rows = self.rows.get(tab, [])
        text = self.searches.get(tab, "").lower()
        shown = [(k, c) for k, c in rows if self.keeps(tab, k)
                 and (not text or text in " ".join(plain(x) for x in c).lower())]
        if tab in self.sorts:
            col, rev = self.sorts[tab]
            empty = [r for r in shown if blank(r[1][col])]  # rows without a value stay at the end
            shown = sorted((r for r in shown if not blank(r[1][col])), key=lambda r: sort_value(r[1][col]),
                           reverse=rev) + empty
        table.clear()
        for key, cells in shown:
            table.add_row(*cells, key=key)
        self.restore(table, keep)
        if tab == self.tab():
            self.update_view_line()

    def update_view_line(self) -> None:
        """The line over the log: how many rows the list shows, and the filter, search and sort on it."""
        tab = self.tab()
        with contextlib.suppress(NoMatches):
            line = self.query_one("#view", Static)
            if tab not in LIST_TABS or tab not in self.rows:
                line.update("")
                return
            total, shown = len(self.rows[tab]), self.query_one(f"#{tab}-table", DataTable).row_count
            bits = [f"{shown} of {total} rows" if shown != total else f"{total} rows"]
            if tab in FILTERS:
                bits.append(f"F4 filter: {dict(FILTERS[tab])[self.filter_of(tab)]}")
            if self.searches.get(tab):
                bits.append(f"F3 search: {self.searches[tab]!r}")
            if tab in self.sorts:
                col, rev = self.sorts[tab]
                bits.append(f"F6 sort: {self.heads[tab][col]} {'↓' if rev else '↑'}")
            line.update(" · ".join(bits))

    def set_filter(self, tab: str, f: str) -> None:
        self.filters[tab] = f
        self.apply_view(tab)
        if tab == "prs":
            self.label_prs()
        self.log_line(f"{dict(self.tabs)[tab]}: {dict(FILTERS[tab])[f]} (F4 picks, v the next one)")
        self.update_detail()

    def action_filter(self) -> None:
        """F4: pick one of the tab's filters."""
        tab = self.tab()
        if tab not in FILTERS:
            return
        now = self.filter_of(tab)

        def picked(f: str | None) -> None:
            if f:
                self.set_filter(tab, f)
                self.focus_table(tab)
        self.pick(f"Filter {dict(self.tabs)[tab]}",
                  [(k, f"{label}{'  (now)' if k == now else ''}") for k, label in FILTERS[tab]], picked)

    def action_filter_next(self) -> None:
        """v: the tab's next filter."""
        tab = self.tab()
        if tab in FILTERS:
            order = [k for k, _ in FILTERS[tab]]
            self.set_filter(tab, order[(order.index(self.filter_of(tab)) + 1) % len(order)])

    def action_pr_filter(self) -> None:
        self.action_tab("prs")
        self.action_filter_next()

    def action_sort(self) -> None:
        """F6: sort by a column; the same column again reverses it, "as loaded" goes back to mason's order."""
        tab = self.tab()
        if tab not in LIST_TABS:
            return
        col, rev = self.sorts.get(tab, (-1, False))
        options = [("-1", "as loaded (mason's order)" + ("  (now)" if col < 0 else ""))]
        for i, head in enumerate(self.heads[tab]):
            now = f"  (now {'↓' if rev else '↑'}, again reverses)" if i == col else ""
            options.append((str(i), f"{head}{now}"))

        def picked(choice: str | None) -> None:
            if not choice:
                return
            i = int(choice)
            if i < 0:
                self.sorts.pop(tab, None)
            else:
                self.sorts[tab] = (i, not rev if i == col else False)
            self.apply_view(tab)
            self.focus_table(tab)
            self.update_detail()
        self.pick(f"Sort {dict(self.tabs)[tab]} by", options, picked)

    def action_search(self) -> None:
        """F3 or /: the search line; the list keeps the rows with its text as you type."""
        if self.tab() not in LIST_TABS:
            return
        search = self.query_one("#search", Search)
        search.add_class("-shown")
        search.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search" and self.tab() in LIST_TABS:
            self.searches[self.tab()] = event.value.strip()
            self.apply_view(self.tab())
            self.update_detail()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self.end_search()

    def end_search(self, clear: bool = False) -> None:
        search = self.query_one("#search", Search)
        if clear or not search.value.strip():
            search.remove_class("-shown")
        self.focus_table()

    def show_search(self) -> None:
        """The search line shows the text of the tab you are on (each tab keeps its own)."""
        with contextlib.suppress(NoMatches):
            search = self.query_one("#search", Search)
            text = self.searches.get(self.tab(), "")
            with search.prevent(Input.Changed):
                search.value = text
            search.set_class(bool(text), "-shown")
            self.update_view_line()

    def action_help(self) -> None:
        tab = self.tab()
        extra = ""
        if tab not in ("settings", "checks") and tab not in self.plugin_tabs:
            extra = f"\nOn {dict(self.tabs).get(tab, tab)}, for the selected row\n  " + self.keys_line(tab).replace(
                "\n", "\n  ") + "\n"
        self.push_screen(Help(extra))

    @work(thread=True, exclusive=True, group="runs")
    def load_runs(self) -> None:
        self.loaded["runs"] = time.time()

        def latest(repo):
            try:
                found = (core.api("GET", f"/repos/{repo}/actions/runs?per_page=1") or {}).get("workflow_runs") or []
            except core.RateLimited:
                raise
            except RuntimeError:
                found = []
            return repo, found[0] if found else None

        try:
            waiting = R.all_waiting(self.repos)
            last = core.pmap(latest, self.repos)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        self.call_from_thread(self.fill_runs, waiting, last)
        self.check_rate()

    def fill_runs(self, waiting, last) -> None:
        with contextlib.suppress(NoMatches):
            self._fill_runs(waiting, last)

    def _fill_runs(self, waiting, last) -> None:
        out = []
        self.runs = {}
        seen = set()
        for repo, run, envs in waiting:
            key = f"{repo}|{run['id']}"
            seen.add(run["id"])
            self.runs[key] = (repo, run, envs)
            state = (Text("waiting, not for you", style=WARNING) if R.is_blocked(envs)
                     else Text("waiting for you", style=f"bold {self.highlight()}"))
            out.append((key, [self.label(repo), state, run.get("name") or "", run.get("head_branch") or "",
                              core.ago(run.get("created_at"))]))
        waiting = [w for w in waiting if not R.is_blocked(w[2])]
        for repo, run in last:
            if not run or run["id"] in seen:
                continue
            key = f"{repo}|{run['id']}"
            self.runs[key] = (repo, run, [])
            state = "waiting" if run.get("status") == "waiting" else (run.get("conclusion") or run.get("status") or "?")
            out.append((key, [self.label(repo), styled(state), run.get("name") or "",
                              run.get("head_branch") or "", core.ago(run.get("created_at"))]))
        self.put_rows("runs", out)
        if len(waiting) != self.last_waiting and waiting:
            self.log_line(f"{len(waiting)} run(s) wait for your approval: Runs tab (3), key a")
        self.last_waiting = len(waiting)
        self.update_title()
        with contextlib.suppress(NoMatches):
            self.query_one("#tabs", TabbedContent).get_tab("runs").label = (
                f"Runs ({len(waiting)} to approve)" if waiting else "Runs")
        self.update_detail()

    @staticmethod
    def restore(table: DataTable, key: str | None) -> None:
        if key is None:
            return
        with contextlib.suppress(RowDoesNotExist):
            table.move_cursor(row=table.get_row_index(key))

    # ── detail pane ───────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.refresh_bindings()
        self.update_detail()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self.refresh_bindings()
        self.show_search()
        core.CACHE = CACHE
        self.load_tab_data()
        self.focus_table()
        self.update_detail()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_menu()

    def update_detail(self) -> None:
        tab, key = self.tab(), self.current_key()
        if not key:
            return
        if tab == "repos":
            self.show("repos", self.repo_text(key))
        elif tab == "prs":
            if key in self.detail_cache:
                self.show("prs", self.detail_cache[key])
            else:
                self.show("prs", "Loading …")
                self.load_pr(key)
        elif tab == "security":
            self.show("security", self.security_text(key))
        elif tab == "packages":
            info = self.packages[key]
            last = info["runs"][0] if info["runs"] else {}
            if f"packages:{key}" in self.detail_cache:
                self.show("packages", self.detail_cache[f"packages:{key}"])
            elif last.get("conclusion") == "failure":
                self.show("packages", P.text(info) + "\n\nLoading what failed …")
                self.load_image_jobs(key, info["repo"], last["id"])
            else:
                self.show("packages", P.text(info))
        elif tab == "releases":
            info = self.releases[key]
            last = info["runs"][0] if info["runs"] else {}
            head = "\n".join(self.version_lines(key))
            head = head + "\n\n" if head else ""
            if f"releases:{key}" in self.detail_cache:
                self.show("releases", head + self.detail_cache[f"releases:{key}"])
            elif last.get("conclusion") == "failure":
                self.show("releases", head + R.release_text(info) + "\n\nLoading what failed …")
                self.load_release_jobs(key, last["id"])
            else:
                self.show("releases", head + R.release_text(info))
        elif tab == "runs":
            if f"runs:{key}" in self.detail_cache:
                self.show("runs", self.detail_cache[f"runs:{key}"])
            else:
                self.show("runs", self.run_text(key) + "\n\nLoading its jobs …")
                self.load_run_jobs(key)
        else:
            cache_key = f"{tab}:{key}"
            if cache_key in self.detail_cache:
                self.show(tab, self.detail_cache[cache_key])
            else:
                self.show(tab, "Checking …" if tab == "checks" else "Loading …")
                self.load_view(tab, key)

    def run_text(self, key: str) -> str:
        repo, run, envs = self.runs[key]
        state = run.get("conclusion") or run.get("status")
        lines = [f"{repo}  {run.get('name')}", run["html_url"], "",
                 f"event    {run.get('event')}", f"ref      {run.get('head_branch')}",
                 f"state    {state}", f"started  {core.ago(run.get('created_at'))}"]
        if run.get("run_attempt", 1) > 1:
            lines.append(f"attempt  {run['run_attempt']}")
        if envs and R.is_blocked(envs):
            lines += ["", f"Waits for: {', '.join(e['name'] for e in envs)}, but you can't approve it:"]
            lines += [f"  {e['blocked']}" for e in envs]
        elif envs:
            lines += ["", f"Waits for your approval: {', '.join(e['name'] for e in envs)}"]
        return "\n".join(lines)

    @work(thread=True, group="detail")
    def load_run_jobs(self, key: str) -> None:
        """The run's jobs under its detail: the steps of a release at a glance, and what failed."""
        repo, run, _ = self.runs[key]
        try:
            jobs = R.run_jobs(repo, run["id"])
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            jobs = [f"error: {err}"]
        text = self.run_text(key) + "\n\nJobs:\n" + "\n".join(f"  {j}" for j in jobs or ["none listed"])
        self.detail_cache[f"runs:{key}"] = text
        self.call_from_thread(self.show_if_current, "runs", key, text)

    def repo_text(self, repo: str) -> str:
        node = self.info.get(repo) or {}
        g = R.group_of(self.cfg, repo)
        tags = [r["tagName"] for r in (node.get("releases") or {}).get("nodes") or []]
        last = core.newest(tags)
        ci = ((((node.get("defaultBranchRef") or {}).get("target") or {}).get("statusCheckRollup")) or {}).get("state")
        branch = (node.get("defaultBranchRef") or {}).get("name") or "main"
        lines = [repo, f"{core.WEB}/{repo}", "", f"group        {g.name if g else '-'}"]
        lines.append(f"released     {last or '-'}" + (f"  {core.WEB}/{repo}/releases/tag/{last}" if last else ""))
        lines += self.version_lines(repo)
        lines.append(f"CI on {branch:<6} {(ci or '-').lower()}  {core.WEB}/{repo}/actions")
        if repo in self.alerts:
            alerts, _ = self.alerts[repo]
            lines.append(f"alerts       {len(alerts) or 'none'}" + ("  (Security tab)" if alerts else ""))
        info = self.releases.get(repo)
        if info and info["missing"]:
            lines.append(f"release      missing {', '.join(info['missing'])}")
        lines += plugins.hook("repo_detail", repo, node)
        lines += ["", "Open PRs:"]
        for p in sorted(((node.get("pullRequests") or {}).get("nodes") or []), key=lambda p: p["number"]):
            lines.append(f"  #{p['number']} {p['title']}{' (draft)' if p['isDraft'] else ''}")
            lines.append(f"     {p['url']}")
        if lines[-1] == "Open PRs:":
            lines.append("  none")
        return "\n".join(lines)

    def version_lines(self, repo: str) -> list[str]:
        """The version file against the latest release, once loaded (a background call per repo)."""
        if not V.source(self.cfg, repo):
            return []
        st = self.versions.get(repo)
        if st is None:
            self.versions[repo] = "…"
            self.load_version(repo)
            return ["version      …"]
        if isinstance(st, str):
            return [f"version      {st}"]
        if not st["version"]:
            return [f"version      none found in {st['file']}"]
        todo = f"  → R releases {st['tag']}" if st["todo"] else "  (released; b bumps it)"
        return [f"version      {st['version']} in {st['file']}{todo}"]

    @work(thread=True, group="version")
    def load_version(self, repo: str) -> None:
        try:
            self.versions[repo] = V.state(self.cfg, repo)
        except core.RateLimited as err:
            self.versions.pop(repo, None)
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            self.versions[repo] = f"error: {err}"
        self.call_from_thread(self.refresh_version, repo)

    def refresh_version(self, repo: str) -> None:
        if self.current_key() == repo and self.tab() in ("repos", "releases"):
            self.update_detail()

    @work(thread=True, group="detail")
    def load_pr(self, key: str, diff: bool = False) -> None:
        repo, number = key.rsplit("#", 1)
        text = core.capture(R.pr_detail, repo, int(number), diff, False)
        if not diff:
            self.detail_cache[key] = text
        self.call_from_thread(self.show_if_current, "prs", key, text)

    @work(thread=True, group="detail")
    def load_view(self, tab: str, key: str) -> None:
        if tab in self.plugin_tabs:
            try:
                text = self.plugin_tabs[tab]["text"](key)
            except Exception as err:
                text = f"error: {err}"
        elif tab == "settings":
            text = self.settings_text(key)
        elif key == "doctor":
            text = core.capture(D.doctor_all, self.cfg, self.repos)
            text += "\nf applies the fixes (doctor --fix) · s opens PRs with the shared files (sync)\n"
        else:
            run = next(fn for k, _, fn in self.checks if k == key)
            try:
                text = run(self.repos)
            except Exception as err:
                text = f"error: {err}"
        self.detail_cache[f"{tab}:{key}"] = text
        self.call_from_thread(self.show_if_current, tab, key, text)

    def show_if_current(self, tab: str, key: str, text: str) -> None:
        if self.tab() == tab and self.current_key(tab) == key:
            self.show(tab, text)

    # ── actions ───────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        """r: everything on the tab you are on, fresh (the Repos and PR list are one query)."""
        self.detail_cache.clear()
        self.versions.clear()
        R._nodes.clear()
        self.loaded.pop(self.tab(), None)
        core.CACHE = None  # r means fresh: no answers from gh's cache
        self.force = True
        self.load_github()
        for screen in self.screen_stack:
            if isinstance(screen, RowPage) and screen.tab == "releases":
                self.load_release_page(screen.key)
        if self.tab() not in ("repos", "prs", "runs", "releases", "packages", "security"):
            self.update_detail()

    def load_tab_data(self) -> None:
        """Runs, releases, packages and security cost API calls per repo: they load the first time you open their tab,
        then on r."""
        tab, force, self.force = self.tab(), self.force, False
        loaders = {"runs": self.load_runs, "releases": self.load_releases, "packages": self.load_packages,
                   "security": self.load_security}
        if tab in loaders and (force or tab not in self.loaded) and self.repos:
            loaders[tab]()

    def rate_limited(self, err: core.RateLimited) -> None:
        self.log_line(f"{err}. r tries again.")
        self.show_rate({"core": {"remaining": 0, "limit": 0}})

    @work(thread=True, exclusive=True, group="rate")
    def check_rate(self) -> None:
        self.call_from_thread(self.show_rate, core.rate_limit())

    def show_rate(self, limits: dict) -> None:
        rest = (limits.get("core") or {}).get("remaining")
        gql = (limits.get("graphql") or {}).get("remaining")
        self.rate = "" if rest is None else f"API {rest}" + (f" · GraphQL {gql}" if gql is not None else "") + " left"
        self.update_title()

    def update_title(self) -> None:
        bits = [self.base_title]
        if self.last_waiting:
            bits.append(f"{self.last_waiting} to approve (3, a)")
        if self.rate:
            bits.append(self.rate)
        self.sub_title = " · ".join(bits)

    def action_open(self) -> None:
        tab, key = self.tab(), self.current_key()
        if not key:
            return
        url = None
        if tab == "repos":
            url = f"{core.WEB}/{key}"
        elif tab == "prs":
            url = self.prs[key]["url"]
        elif tab == "runs":
            url = self.runs[key][1]["html_url"]
        elif tab == "releases":
            url = self.release_url(key)
        elif tab == "packages":
            url = self.packages[key]["url"]
        elif tab == "security":
            alerts = self.alerts.get(key, ([], []))[0]
            url = alerts[0]["url"] if len(alerts) == 1 else f"{core.WEB}/{key}/security"
        elif tab == "checks":
            src = self.cfg.source
            url = f"{core.WEB}/{src.where}" if src and not src.local else None
        elif tab in self.plugin_tabs and self.plugin_tabs[tab].get("url"):
            url = self.plugin_tabs[tab]["url"](key)
        if url:
            self.action_open_link(url)

    def action_fix(self) -> None:
        if self.tab() != "checks" or self.current_key() != "doctor":
            self.log_line("f fixes drift from the Checks tab › Defaults")
            return
        self.confirm_then("Apply the defaults' settings, security and rulesets (and each repo's own rulesets\n"
                          "files) to every repo that drifted? (mason doctor --fix)",
                          D.doctor_all, self.cfg, list(self.repos), True)

    def action_sync(self) -> None:
        if self.tab() != "checks" or self.current_key() != "doctor":
            self.log_line("s syncs files from the Checks tab › Defaults")
            return

        def sync_all(repos) -> None:
            core.for_each(repos, D.sync, D.sync_ctx(self.cfg, repos))
        self.confirm_then("Open a PR in each repo whose shared files differ from its group's? (mason sync)",
                          sync_all, list(self.repos))

    def get_system_commands(self, screen):
        yield from super().get_system_commands(screen)
        commands = [
            ("Auto-merge all ready PRs", "A · every ready PR merges once its checks pass", self.action_merge_all),
            ("Filter pull requests", "v · all, auto-merge, recently merged, drafts", self.action_pr_filter),
            ("Filter the list", "F4 · the filters of the tab you are on", self.action_filter),
            ("Search the list", "F3 or / · only rows with the text stay", self.action_search),
            ("Sort the list", "F6 or > · by a column, again reverses it", self.action_sort),
            ("Every key", "F1 or ?", self.action_help),
            ("Approve waiting run", "a · an environment you review or an outside contributor's run",
             self.action_approve),
            ("Check defaults", "Checks › Defaults (doctor)", lambda: self.show_check("doctor")),
            ("Fix drift", "f · mason doctor --fix (Checks tab)", self.action_fix),
            ("Sync shared files", "s · PRs with each group's files", self.action_sync),
            ("Security alerts", "4 · Dependabot, secret and code scanning", lambda: self.action_tab("security")),
            ("Switch theme", "t", self.action_toggle_theme),
        ]
        for title, help_text, callback in commands:
            yield SystemCommand(title, help_text, callback)
        for key, label, _ in self.checks[1:]:
            yield SystemCommand(f"Check: {label}", "Checks tab", lambda k=key: self.show_check(k))
        for title, help_text, fn in plugins.hook("tui_commands"):
            yield SystemCommand(title, help_text, lambda fn=fn: fn(self))

    def show_check(self, key: str) -> None:
        self.action_tab("checks")
        self.restore(self.query_one("#checks-table", DataTable), key)

    def confirm_then(self, question: str, job, *args) -> None:
        if self.dry_run:
            question += "\n\n(dry run: only shows what it would do)"

        def done(yes: bool | None) -> None:
            if yes:
                self.do_job(job, *args)
        self.push_screen(Confirm(question), done)

    @work(thread=True, group="action")
    def do_job(self, job, *args) -> None:
        text = core.capture(job, *args)
        self.call_from_thread(self.after_action, text)

    def after_action(self, text: str) -> None:
        if not self.tab():
            return
        for screen in self.screen_stack:
            if isinstance(screen, RowPage):
                screen.call_after_refresh(screen.fill_items)
        for line in text.splitlines():
            if line.strip():
                self.log_line(line)
        self.action_refresh()

    def action_merge(self, now: bool) -> None:
        tab, key = self.tab(), self.current_key()
        if not key or tab not in ("repos", "prs"):
            return
        if tab == "prs":
            p = self.prs[key]
            if p.get("merged"):
                self.log_line(f"{p['repo']}#{p['number']} is already merged")
                return
            repo = p["repo"]
            ctx = {"pick": {repo: [p["number"]]}, "now": now, "ready": False}
            what = f"{'Merge now' if now else 'Turn on auto-merge for'} {repo}#{p['number']}\n{p['title']}?"
        else:
            repo = key
            ctx = {"pick": {}, "now": now, "ready": False}
            what = f"{'Merge now' if now else 'Auto-merge'} every ready PR in {repo}?"
        self.confirm_then(what, R.merge, repo, ctx)

    def action_merge_all(self) -> None:
        ready = [p for p in self.prs.values() if not p["isDraft"] and not p.get("merged")
                 and p.get("mergeable") != "CONFLICTING" and not p.get("autoMergeRequest")]
        if not ready:
            self.log_line("No ready PRs without auto-merge (drafts and conflicts are left alone)")
            return
        pick: dict[str, list[int]] = {}
        for p in ready:
            pick.setdefault(p["repo"], []).append(p["number"])
        names = "\n".join(f"  {self.label(p['repo'])}#{p['number']} {p['title'][:60]}" for p in ready[:15])
        if len(ready) > 15:
            names += f"\n  … and {len(ready) - 15} more"

        def merge_all(pick: dict[str, list[int]]) -> None:
            core.for_each(list(pick), R.merge, {"pick": pick, "now": False, "ready": False})
        self.confirm_then(f"Turn on auto-merge for {len(ready)} ready PR(s)? Each merges once its checks pass.\n\n"
                          f"{names}", merge_all, pick)

    def action_ready(self) -> None:
        key = self.current_key()
        if self.tab() != "prs" or not key:
            self.log_line("y marks a draft PR ready: pick one in the Pull requests tab (2)")
            return
        p = self.prs[key]
        if not p["isDraft"]:
            self.log_line(f"{p['repo']}#{p['number']} is already {'merged' if p.get('merged') else 'ready for review'}")
            return

        def ready(repo: str, number: int) -> None:
            if core.dry_run():
                core.say(f"[dry-run] mark {repo}#{number} ready for review")
            else:
                core.transport().pr_ready(repo, int(number))
                core.say(f"{repo}#{number} is ready for review")
        self.confirm_then(f"Mark {p['repo']}#{p['number']} ready for review?", ready, p["repo"], p["number"])

    def action_diff(self) -> None:
        key = self.current_key()
        if self.tab() == "prs" and key:
            self.show("prs", "Loading diff …")
            self.load_pr(key, diff=True)

    def selected_pr(self) -> dict | None:
        key = self.current_key()
        if self.tab() != "prs" or not key:
            return None
        p = self.prs[key]
        if p.get("merged"):
            self.log_line(f"{p['repo']}#{p['number']} is merged")
            return None
        return p

    def action_update_branch(self) -> None:
        p = self.selected_pr()
        if p:
            self.detail_cache.pop(f"{p['repo']}#{p['number']}", None)
            self.confirm_then(f"Merge {p['baseRefName'] if p.get('baseRefName') else 'the base branch'} into "
                              f"{p['repo']}#{p['number']}'s branch (Update branch)?\n{p['title']}",
                              R.update_branch, p["repo"], p["number"])

    def action_close_pr(self) -> None:
        p = self.selected_pr()
        if p:
            self.confirm_then(f"Close {p['repo']}#{p['number']} without merging?\n{p['title']}",
                              R.close_pr, p["repo"], p["number"])

    def action_comment(self) -> None:
        p = self.selected_pr()
        if not p:
            return

        def write(text: str | None) -> None:
            if text and text.strip():
                self.detail_cache.pop(f"{p['repo']}#{p['number']}", None)
                self.do_job(R.comment_pr, p["repo"], p["number"], text.strip())
        self.ask(f"Comment on {p['repo']}#{p['number']} (Enter posts it, Esc cancels)", write)

    def action_logs(self) -> None:
        key = self.current_key()
        if self.tab() != "runs" or not key:
            return
        repo, run, _ = self.runs[key]
        if (run.get("conclusion") or "") not in R.FAILED:
            self.log_line(f"{run.get('name')} didn't fail: no failed log to show")
            return
        self.show("runs", self.run_text(key) + "\n\nLoading the failed log …")
        self.load_failed_log(key)

    @work(thread=True, group="detail")
    def load_failed_log(self, key: str) -> None:
        repo, run, _ = self.runs[key]
        try:
            lines = R.failed_log(repo, run["id"])
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            lines = [f"error: {err}"]
        text = self.run_text(key) + "\n\nFailed log:\n" + "\n".join(lines)
        self.call_from_thread(self.show_if_current, "runs", key, text)

    def action_cancel_run(self) -> None:
        key = self.current_key()
        if self.tab() != "runs" or not key:
            return
        repo, run, _ = self.runs[key]
        if run.get("status") not in R.ACTIVE:
            self.log_line(f"{run.get('name')} isn't running ({run.get('conclusion') or run.get('status')})")
            return
        self.detail_cache.pop(f"runs:{key}", None)
        self.confirm_then(f"Cancel {repo} {run.get('name')} on {run.get('head_branch')}?", R.cancel_run, repo, run)

    def action_approve(self) -> None:
        key = self.current_key()
        if self.tab() != "runs" or not key:
            self.log_line("a approves a run waiting for you: Runs tab (3)")
            return
        repo, run, envs = self.runs[key]
        if not envs:
            self.log_line("That run isn't waiting for your approval")
            return
        if R.is_blocked(envs):
            for e in envs:
                self.log_line(f"You can't approve it: {e['blocked']}")
            return

        def approve(repo, run, envs) -> None:
            if core.dry_run():
                core.say(f"[dry-run] approve {repo} {run.get('head_branch')}")
            else:
                R.approve_run(repo, run, envs)
                core.say(f"approved {', '.join(e['name'] for e in envs)} for {repo} {run.get('head_branch')}")
        if R.RUN_APPROVAL in envs:
            question = (f"Let {repo} run {run.get('name')} on {run.get('head_branch')}?\n"
                        f"It comes from an outside contributor: check the PR's changes first.\n{run.get('html_url')}")
        else:
            question = f"Approve {', '.join(e['name'] for e in envs)} for {repo} {run.get('head_branch')}?"
        self.confirm_then(question, approve, repo, run, envs)


    def action_rerun(self) -> None:
        key = self.current_key()
        if self.tab() != "runs" or not key:
            self.log_line("x re-runs a failed run: Runs tab (3)")
            return
        repo, run, _ = self.runs[key]
        state = run.get("conclusion") or run.get("status")
        if state not in R.FAILED:
            self.log_line(f"{run.get('name')} didn't fail ({state}): nothing to re-run")
            return
        self.detail_cache.pop(f"runs:{key}", None)
        self.confirm_then(f"Re-run the failed jobs of {repo} {run.get('name')} on {run.get('head_branch')}?\n"
                          "Jobs that passed and approvals already given stay as they are.",
                          R.rerun_failed, repo, run)

    def version_repo(self) -> str | None:
        """The repo of the selected row on Repos or Releases, when its group names a version file."""
        key = self.current_key()
        if self.tab() not in ("repos", "releases") or not key:
            self.log_line("b bumps and R releases the selected repo: Repos (1) or Releases (4)")
            return None
        if not V.source(self.cfg, key):
            self.log_line(f"{key}: its group names no version file (version = \"...\" in mason.toml)")
            return None
        return key

    def action_bump(self) -> None:
        repo = self.version_repo()
        if not repo:
            return

        def picked(level: str | None) -> None:
            if level:
                self.confirm_then(f"Open a PR in {repo} that raises the version ({level}) and adds the changelog "
                                  "section?\nAfter its merge, R pushes the tag.", V.bump, self.cfg, repo, level)
        self.push_screen(Pick(f"Bump {repo}", [("patch", "patch: fixes (1.2.3 → 1.2.4)"),
                                               ("minor", "minor: new features (1.2.3 → 1.3.0)"),
                                               ("major", "major: breaking changes (1.2.3 → 2.0.0)")]), picked)

    def action_release(self) -> None:
        repo = self.version_repo()
        if repo:
            self.log_line(f"{repo}: reading its version …")
            self.release_state(repo)

    @work(thread=True, group="action")
    def release_state(self, repo: str) -> None:
        try:
            st = V.state(self.cfg, repo)
        except RuntimeError as err:
            self.call_from_thread(self.log_line, f"{repo}: {err}")
            return
        self.call_from_thread(self.confirm_release, st)

    def confirm_release(self, st: dict) -> None:
        repo = st["repo"]
        if not st["todo"]:
            self.do_job(V.release, self.cfg, repo)  # says where the release stands, pushes nothing
            return
        self.confirm_then(f"Push the tag {st['tag']} on {repo} {st['base']}?\n{st['file']} says {st['version']}, "
                          f"latest release {st['released'] or 'none'}. The repo's release workflow takes it from "
                          "there.", V.release, self.cfg, repo)

    @work(thread=True, group="relpage")
    def load_release_page(self, repo: str) -> None:
        """The release page of a repo (its recent releases, the newest in full), for its RowPage."""
        try:
            text = REL.page(self.cfg, repo)["text"]
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError as err:
            text = f"{repo}\n\nerror: {err}"
        head = "\n".join(self.version_lines(repo))
        self.detail_cache[f"relpage:{repo}"] = (head + "\n\n" if head else "") + text
        self.call_from_thread(self.update_detail)

    def action_new_release(self) -> None:
        """N: create a release by hand. Asks the tag (the next patch is suggested), title, notes and kind."""
        key = self.current_key()
        if self.tab() not in ("repos", "releases") or not key:
            self.log_line("N creates a release for the selected repo: Repos (1) or Releases (4)")
            return
        self.log_line(f"{key}: reading its releases …")
        self.suggest_release(key)

    def action_new_tag(self) -> None:
        """T: push only a tag (the next patch is suggested); a workflow that publishes on tags does the rest."""
        key = self.current_key()
        if self.tab() not in ("repos", "releases") or not key:
            self.log_line("T pushes a tag for the selected repo: Repos (1) or Releases (4)")
            return
        self.log_line(f"{key}: reading its releases and workflows …")
        self.suggest_release(key, only_tag=True)

    @work(thread=True, group="action")
    def suggest_release(self, repo: str, only_tag: bool = False) -> None:
        workflow = ""
        try:
            tag = REL.suggest_from(REL.recent(repo))
            if only_tag:
                workflow = REL.publishing_workflow(repo)
        except core.RateLimited as err:
            self.call_from_thread(self.rate_limited, err)
            return
        except RuntimeError:
            tag = "v0.1.0"
        if only_tag:
            self.call_from_thread(self.ask_tag, repo, tag, workflow)
        else:
            self.call_from_thread(self.ask_release, repo, tag)

    def ask_tag(self, repo: str, suggested: str, workflow: str) -> None:
        def tagged(tag: str | None) -> None:
            if not tag or not tag.strip():
                return
            try:
                tag = REL.valid_tag(tag)
            except RuntimeError as err:
                self.log_line(str(err))
                return
            then = (f"{workflow} creates the release from it." if workflow
                    else "No workflow of this repo creates a release from it (N makes a release instead).")
            self.detail_cache.pop(f"relpage:{repo}", None)
            self.confirm_then(f"Push the tag {tag} on the default branch of {repo}?\n{then}", REL.push_tag, repo, tag)
        self.push_screen(Ask(f"New tag on {repo} (only the tag is pushed)", value=suggested), tagged)

    def ask_release(self, repo: str, suggested: str) -> None:
        got: dict = {}

        def tagged(tag: str | None) -> None:
            if not tag or not tag.strip():
                return
            try:
                got["tag"] = REL.valid_tag(tag)
            except RuntimeError as err:
                self.log_line(str(err))
                return
            self.push_screen(Ask(f"Title of {got['tag']} (Enter keeps the tag)", value=got["tag"]), titled)

        def titled(title: str | None) -> None:
            if title is None:
                return
            got["title"] = title.strip() or got["tag"]
            self.push_screen(Ask("Notes (one line; empty: GitHub writes them from the merged PRs)"), noted)

        def noted(notes: str | None) -> None:
            if notes is None:
                return
            got["notes"] = notes.strip()
            self.push_screen(Pick(f"{repo} {got['tag']}: publish it how?", [
                ("published", "published: a release everyone sees"),
                ("prerelease", "pre-release: published, marked not ready for production"),
                ("draft", "draft: only people with push access see it, publish it later")]), kinded)

        def kinded(kind: str | None) -> None:
            if not kind:
                return
            draft, pre = kind == "draft", kind == "prerelease"
            notes = got["notes"]
            self.detail_cache.pop(f"relpage:{repo}", None)
            self.confirm_then(f"Create the {kind} release {got['tag']} of {repo}?\nTitle: {got['title']}\n"
                              + (f"Notes: {notes}" if notes else "Notes: written by GitHub from the merged PRs")
                              + "\nA new tag is made on the default branch.",
                              REL.create, repo, got["tag"], got["title"], notes, draft, pre, "", not notes)
        self.push_screen(Ask(f"New release of {repo}: tag", value=suggested), tagged)

    # ── settings ──────────────────────────────────────────────────────────

    def settings_text(self, key: str) -> str:
        cfg = self.cfg
        if key == "defaults":
            src = cfg.source
            lines = [f"Defaults: {src or 'none'}", ""]
            if src and not src.local:
                lines.append(f"{core.WEB}/{src.where}")
            lines += ["", "Enter: use another defaults repo (owner/repo) or a local folder.",
                      "Every repo is checked against its mason.toml (Checks tab)."]
            return "\n".join(lines)
        if key == "orgs":
            mine = config.user_config().get("orgs") or []
            return "\n".join([f"Orgs: {', '.join(cfg.orgs) or 'none'}", "",
                              f"from the defaults: {', '.join(config.load().raw.get('orgs') or []) or 'none'}",
                              f"your own:          {', '.join(mine) or 'none'}", "",
                              "Enter: set your own orgs (added to the defaults' orgs)."])
        if key == "groups":
            groups = cfg.groups_for(self.repos)
            out = [f"{len(groups)} group(s), first match wins (an org's .mason before your catch-all):", ""]
            for g in groups:
                count = sum(1 for r in self.repos if (x := R.group_of(cfg, r)) and x.name == g.name)
                out.append(f"{g.name}  ({count} repos)")
                out.append(f"  match     {g.match or 'the rest'}")
                if g.origin:
                    out.append(f"  from org  {g.origin}")
                if g.reference:
                    out.append(f"  from      {g.reference}  {core.WEB}/{g.reference}")
                for label, value in (("settings", g.settings), ("security", g.security), ("require", g.require),
                                     ("rulesets", g.rulesets), ("files", g.files),
                                     ("version", g.version), ("changelog", g.changelog)):
                    if value:
                        out.append(f"  {label:<9} {value}")
                out.append("")
            out.append("Groups live in mason.toml in the defaults repo.")
            return "\n".join(out)
        if key == "plugins":
            out = [f"{p.name}: {getattr(p, 'description', '')}" for p in plugins.load()] or ["No plugins."]
            out += [f"broken: {e}" for e in plugins.errors]
            out += ["", "Add one: pipx inject mason-cli <package>", "Limit them: MASON_PLUGINS=a,b or none"]
            return "\n".join(out)
        if key == "theme":
            return f"Theme: {self.theme}\n\nEnter or t: next theme ({', '.join(t.name for t in self.themes)})"
        path = config.config_file()
        text = path.read_text() if path.exists() else "(none yet: mason init writes it)"
        return f"{path}\n\n{text}"

    def setting(self, key: str) -> None:
        if key == "theme":
            self.action_toggle_theme()
        elif key == "defaults":
            self.ask("Defaults repo (owner/repo) or local folder with mason.toml", self.set_defaults)
        elif key == "orgs":
            mine = ", ".join(config.user_config().get("orgs") or [])
            self.push_screen(Ask("Your own orgs or users, comma separated (empty: none)", value=mine), self.set_orgs)
        else:
            self.update_detail()

    def save_config(self, **changes) -> None:
        values = {**config.user_config(), **changes}
        config.save_user_config(values)
        config._loaded.clear()
        try:
            self.cfg = config.load()
        except RuntimeError as err:
            self.log_line(f"Settings: {err}")
            return
        self.base_title = ", ".join(self.cfg.orgs) or "no orgs"
        self.log_line(f"saved {config.config_file()}")
        self.action_refresh()

    def set_defaults(self, value: str | None) -> None:
        if value:
            source = config.Source(value)
            if config.read_defaults(source)[1] is None:
                self.log_line(f"No {config.DEFAULTS_FILE} in {value} (mason init {value} starts one)")
                return
            self.save_config(defaults=value)

    def set_orgs(self, value: str | None) -> None:
        if value is not None:
            self.save_config(orgs=[o.strip() for o in value.split(",") if o.strip()])

    # ── Enter: what you can do with the selected row ──────────────────────

    def menu_items(self) -> list[tuple[str, str, object]]:
        tab, key = self.tab(), self.current_key()
        if not key:
            return []
        items: list[tuple[str, str, object]] = []
        if tab == "settings":
            label = {"defaults": "Change the defaults repo", "orgs": "Change your orgs",
                     "theme": "Next theme"}.get(key)
            if label:
                items.append(("setting", label, lambda: self.setting(key)))
        if tab == "repos":
            items += [("merge", "Auto-merge its ready PRs (m)", lambda: self.action_merge(False)),
                      ("merge-now", "Merge its ready PRs now (M)", lambda: self.action_merge(True)),
                      ("doctor", "Check it against the defaults", lambda: self.do_job(D.doctor_all, self.cfg, [key])),
                      ("fix", "Apply the defaults to it (doctor --fix)", lambda: self.confirm_then(
                          f"Apply the defaults' settings, security and rulesets to {key}?",
                          D.doctor_all, self.cfg, [key], True)),
                      ("sync", "Open a PR with its group's files (sync)", lambda: self.sync_repos([key])),
                      ("security", "Its security alerts", lambda: self.goto("security", key)),
                      ("actions", "Its Actions page", lambda: self.action_open_link(f"{core.WEB}/{key}/actions"))]
        elif tab == "prs":
            p = self.prs[key]
            if not p.get("merged"):
                items += [("merge", "Turn on auto-merge (m)", lambda: self.action_merge(False)),
                          ("merge-now", "Merge now (M)", lambda: self.action_merge(True))]
                if p["isDraft"]:
                    items.append(("ready", "Mark ready for review (y)", self.action_ready))
                items += [("update", "Update its branch from the base (u)", self.action_update_branch),
                          ("comment", "Comment on it (c)", self.action_comment),
                          ("close", "Close it without merging (C)", self.action_close_pr)]
                items.append(("diff", "Show the diff (d)", self.action_diff))
            items.append(("checks", "Its checks page", lambda: self.action_open_link(f"{p['url']}/checks")))
        elif tab == "runs":
            run, envs = self.runs[key][1], self.runs[key][2]
            if envs and not R.is_blocked(envs):
                items.append(("approve", "Approve (a)", self.action_approve))
            if (run.get("conclusion") or "") in R.FAILED:
                items += [("rerun", "Re-run its failed jobs (x)", self.action_rerun),
                          ("logs", "Show the end of the failed log (l)", self.action_logs)]
            if run.get("status") in R.ACTIVE:
                items.append(("cancel", "Cancel it (C)", self.action_cancel_run))
        if tab in ("repos", "releases") and V.source(self.cfg, key):
            items += [("bump", "Bump its version: PR with version + changelog (b)", self.action_bump),
                      ("release", "Release: tag v<version> when it is new (R)", self.action_release)]
        if tab in ("repos", "releases"):
            items.append(("new-release", "Create a release: tag, title, notes, draft (N)", self.action_new_release))
            items.append(("new-tag", "Push a tag only: its workflow makes the release (T)", self.action_new_tag))
        if tab == "releases":
            runs = self.releases[key]["runs"]
            if runs:
                items.append(("run", f"Its last release run ({R.run_state(runs[0])})",
                              lambda: self.action_open_link(runs[0]["html_url"])))
            items.append(("all", "All its releases", lambda: self.action_open_link(f"{core.WEB}/{key}/releases")))
        elif tab == "packages":
            info = self.packages[key]
            if info["runs"]:
                items.append(("run", f"Its last image build ({R.run_state(info['runs'][0])})",
                              lambda: self.action_open_link(info["runs"][0]["html_url"])))
            if info["repo"]:
                items.append(("repo", f"Its repo {info['repo']}", lambda: self.goto("repos", info["repo"])))
        elif tab == "checks" and key == "doctor":
            items += [("fix", "Apply the defaults where repos drifted (f)", self.action_fix),
                      ("sync", "Open PRs with the shared files (s)", self.action_sync),
                      ("again", "Check again", self.recheck)]
        for i, (k, description, *_) in enumerate(self.plugin_keys):
            if tab in self.key_tabs(i):
                items.append((f"plugin-{i}", f"{description} ({k})", lambda i=i: self.action_plugin_key(i)))
        if tab not in ("settings",):
            items += [("open", "Open in the browser (o)", self.action_open)]
        return items

    def action_menu(self) -> None:
        items = self.menu_items()
        if not items:
            return
        actions = {k: fn for k, _, fn in items}
        title = self.current_key() or ""
        if self.tab() == "prs":
            p = self.prs[title]
            title = f"{self.label(p['repo'])}#{p['number']} {p['title'][:60]}"
        elif self.tab() == "runs":
            repo, run, _ = self.runs[title]
            title = f"{self.label(repo)} · {run.get('name')} · {run.get('head_branch')}"
        if self.tab() in PAGE_TABS:
            tab_title = dict(self.tabs).get(self.tab(), "")
            self.push_screen(RowPage(self.tab(), self.current_key() or "", f"{tab_title} › {title}"))
            return

        def chosen(choice: str | None) -> None:
            if choice:
                actions[choice]()
        self.push_screen(Pick(title, [(k, label) for k, label, _ in items]), chosen)

    def goto(self, tab: str, key: str) -> None:
        self.action_tab(tab)
        with contextlib.suppress(NoMatches):
            self.restore(self.query_one(f"#{tab}-table", DataTable), key)

    def recheck(self) -> None:
        self.detail_cache.pop(f"{self.tab()}:{self.current_key()}", None)
        self.update_detail()

    def sync_repos(self, repos: list[str]) -> None:
        def sync_all(repos) -> None:
            core.for_each(repos, D.sync, D.sync_ctx(self.cfg, repos))
        self.confirm_then(f"Open a PR in {', '.join(repos) if len(repos) < 4 else f'{len(repos)} repos'} where the "
                          "shared files differ from the group's? (mason sync)", sync_all, list(repos))

    # ── new repo ──────────────────────────────────────────────────────────

    def action_new_repo(self) -> None:
        one = self.cfg.orgs[0] if len(self.cfg.orgs) == 1 else ""

        def named(name: str | None) -> None:
            if not name:
                return
            repo = name if "/" in name or not one else f"{one}/{name}"
            if "/" not in repo:
                self.log_line("Name the owner too: owner/name")
                return
            match = self.cfg.group_of(repo)
            options = [("@match", f"by match: {match.name if match else 'no group'}")] + [
                (g.name, g.name) for g in self.cfg.groups_for([repo]) if not match or g.name != match.name]
            self.push_screen(Pick(f"Group for {repo}", options), lambda g: grouped(repo, g, match))

        def grouped(repo: str, group: str | None, match) -> None:
            if not group:
                return
            group = "" if group == "@match" else group
            name = group or (match.name if match else "")
            self.push_screen(Pick(f"{repo} in group {name or '(none)'}: visibility",
                                  [("private", "private"), ("public", "public")]),
                             lambda vis: visible(repo, group or None, vis))

        def visible(repo: str, group: str | None, vis: str | None) -> None:
            if vis:
                self.confirm_then(f"Create {repo} ({vis}) and set it up the way its group wants?\n"
                                  "(from the reference repo if it is a template, the group's files, settings, "
                                  "security and rulesets)", D.create_repo, self.cfg, repo, group, vis == "public")
        self.ask("New repo: owner/name" + (f" (or just a name for {one})" if one else ""), named)


def run(cfg: Config, dry_run: bool = False, group: str | None = None) -> None:
    core.set_dry_run(dry_run)
    core.CACHE = CACHE
    core.WORKERS = 4  # fewer calls at once: GitHub's secondary rate limit watches bursts
    MasonTui(cfg, dry_run=dry_run, group=group).run()
