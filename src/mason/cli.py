"""mason: your GitHub repos, across orgs, from one terminal.

Repos are named as owner/name, as a bare name when only one repo has it, or by
their group alias (the group's prefix dropped). name#12 is a PR. No names: every
repo of your orgs. --org and --group narrow that down.

  mason init your-org/your-defaults   point mason at a defaults repo (or a local folder)
  mason status | prs | runs           what's going on
  mason stats [--days 30]             PRs merged, time to merge, CI success, releases
  mason rerun name [--run ID]         re-run the failed jobs of a run
  mason releases [--problems]         latest releases, missing assets, failed release runs
  mason packages [--problems]         container images: newest tags, release images, image builds
  mason bump name [patch|minor|1.4.0] a PR with the next version and its changelog section
  mason release [names] [--yes]       tag v<version> where it is newer than the latest release
  mason release new name v1.4.0       create a release by hand (--title, --notes, --draft, --prerelease)
  mason release view name [tag]       one release in full, with the repo's recent releases
  mason pr web#12 [--diff]            one PR in detail
  mason merge [names] [--now]         auto-merge open PRs (drafts and conflicts stay)
  mason approve                       runs waiting for you (environments, outside contributors)
  mason security                      Dependabot, secret and code scanning alerts
  mason doctor [names] [--fix]        settings, security, rulesets and files against the defaults
  mason issues [names] [--mine]       open issues; mason issue new|view|close|comment name[#12] ...
  mason workflows [names] [--fix]     unpinned actions, write-all tokens, script injection; --fix: PRs that pin
  mason sync [names]                  PRs that bring shared files in line
  mason new owner/name [--group G]    a new repo, set up the way its group wants
  mason tui                           all of it, full screen
  mason login https://mason          go through Mason (the web app) with an API key: no gh needed
  mason market [install NAME]         plugins from the signed plugin index
  mason serve [--check]            run Mason, the web app (the [server] extra)
"""

from __future__ import annotations

import argparse
import sys

from mason import __version__, config, core, defaults, issues, packages, plugins, repos, versions, workflows

ON_REPOS = ("status", "prs", "merge", "runs", "rerun", "releases", "packages", "approve", "security", "doctor", "sync",
            "repos", "stats", "workflows", "issues")


def common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--org", action="append", metavar="ORG", help="only this org or user (repeatable)")
    common.add_argument("--group", help="only repos of this group")
    common.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    return common


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    p = argparse.ArgumentParser(prog="mason", description=__doc__.split("\n\n", 1)[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--version", action="version", version=f"mason {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = common_parser()
    names = "owner/name, name, group alias; name#12 for one PR"
    helps = {
        "status": "one line per repo: group, release, PRs, CI",
        "prs": "open PRs with checks, reviews, conflicts, auto-merge",
        "merge": "auto-merge (or --now) open PRs",
        "runs": "latest workflow runs",
        "rerun": "re-run the failed jobs of the newest failed run",
        "releases": "latest release, its assets and release runs",
        "packages": "container images: newest tags, release images, image builds",
        "approve": "approve runs waiting for you",
        "security": "open security alerts",
        "doctor": "check repos against the defaults",
        "sync": "open PRs with the group's shared files",
        "repos": "the repos mason sees and their groups",
        "stats": "PRs merged, time to merge, CI success, releases over the last days",
        "workflows": "risky workflows: unpinned actions, write-all tokens, script injection",
        "issues": "open issues across repos, by label, assignee or age",
    }
    for cmd in ON_REPOS:
        sp = sub.add_parser(cmd, parents=[common], help=helps[cmd])
        sp.add_argument("repos", nargs="*", help=names)
        if cmd == "merge":
            sp.add_argument("--now", action="store_true", help="merge right away, not via auto-merge")
            sp.add_argument("--ready", action="store_true", help="mark named draft PRs ready first")
            sp.add_argument("--method", choices=("squash", "merge", "rebase"), default="squash")
        if cmd == "approve":
            sp.add_argument("--yes", action="store_true", help="approve without asking")
        if cmd == "rerun":
            sp.add_argument("--run", type=int, help="this run id instead of the newest failed one")
            sp.add_argument("--yes", action="store_true", help="re-run without asking")
        if cmd == "runs":
            sp.add_argument("--limit", type=int, default=3)
            sp.add_argument("--failed", action="store_true")
        if cmd in ("releases", "packages"):
            sp.add_argument("--problems", action="store_true", help="only what's missing or failed")
        if cmd == "doctor":
            sp.add_argument("--fix", action="store_true", help="apply settings and rulesets where they drifted")
        if cmd == "issues":
            sp.add_argument("--state", choices=("open", "closed", "all"), default="open")
            sp.add_argument("--label", action="append", metavar="LABEL", help="only with this label (repeatable)")
            sp.add_argument("--assignee", help="only assigned to this login (none: unassigned, *: any)")
            sp.add_argument("--mine", action="store_true", help="only assigned to you")
            sp.add_argument("--stale", type=int, default=0, metavar="DAYS", help="only untouched for DAYS days")
            sp.add_argument("--limit", type=int, default=20, help="per repo (max 100)")
        if cmd == "workflows":
            sp.add_argument("--fix", action="store_true", help="open a PR per repo that pins the actions to SHAs")
        if cmd == "stats":
            sp.add_argument("--days", type=int, default=30)
        if cmd == "sync":
            sp.add_argument("--only", action="append", metavar="PATH", help="only this file (repeatable)")
    sp = sub.add_parser("bump", parents=[common], help="open a PR with the next version and changelog section")
    sp.add_argument("repos", nargs="+", metavar="name", help=f"{names}; the last may be the level")
    sp.add_argument("--auto-merge", action="store_true", help="merge the PR once its checks pass")
    sp = sub.add_parser("release", parents=[common], help="tag v<version> where it is newer than the latest release")
    sp.add_argument("repos", nargs="*", help=f"{names}; none: list what each repo would release")
    sp.add_argument("--yes", action="store_true", help="push the tags without asking")
    sp.add_argument("--list", action="store_true", help="only list versions against the latest releases")
    sp.add_argument("--title", default="", help="new: the release title (default: the tag)")
    sp.add_argument("--notes", default="", help="new: the release notes, Markdown (- reads them from stdin)")
    sp.add_argument("--draft", action="store_true", help="new: save it as a draft")
    sp.add_argument("--prerelease", action="store_true", help="new: mark it as a pre-release")
    sp.add_argument("--target", default="", help="new: branch or commit for a new tag (default: the default branch)")
    sp.add_argument("--generate-notes", action="store_true", help="new: GitHub writes the notes from merged PRs")
    sp = sub.add_parser("pr", parents=[common], help="one PR in detail")
    sp.add_argument("target", help="name#12 or a PR URL")
    sp.add_argument("--diff", action="store_true")
    sp.add_argument("--body", action="store_true", help="the whole description")
    sp = sub.add_parser("issue", parents=[common], help="create, show, close, reopen or comment on an issue")
    sp.add_argument("action", choices=("new", "view", "close", "reopen", "comment"))
    sp.add_argument("target", help="new: the repo; else name#12 or an issue URL")
    sp.add_argument("text", nargs="?", default="", help="new: the title; comment: the comment")
    sp.add_argument("--body", default="", help="new: the description (- reads it from stdin)")
    sp.add_argument("--label", action="append", metavar="LABEL", help="new: a label (repeatable)")
    sp.add_argument("--assignee", action="append", metavar="LOGIN", help="new: assign (repeatable)")
    sp.add_argument("--not-planned", action="store_true", help="close: as not planned")
    sp = sub.add_parser("open", parents=[common], help="print (and open or copy) a repo or PR link")
    sp.add_argument("target", help="name or name#12")
    sp.add_argument("--actions", action="store_true")
    sp.add_argument("--releases", action="store_true")
    sp = sub.add_parser("init", help="point mason at a defaults repo (owner/repo or a local folder)")
    sp.add_argument("defaults", help="owner/repo or a folder with mason.toml")
    sp.add_argument("--ref", help="branch or tag of the defaults repo")
    sp.add_argument("--org", action="append", metavar="ORG", help="orgs for a new mason.toml")
    sp = sub.add_parser("new", parents=[common], help="create a repo the way its group wants it")
    sp.add_argument("repo", help="owner/name (name alone when mason knows one org)")
    sp.add_argument("--public", action="store_true", help="public (default: private)")
    sp.add_argument("--description", default="")
    sub.add_parser("config", help="show where mason looks and what it found")
    sp = sub.add_parser("login", help="sign in to Mason (the web app) with an API key")
    sp.add_argument("url", help="Mason's URL, e.g. https://mason.mortar.ch")
    sp.add_argument("--read", action="store_true", help="a read-only key")
    sp.add_argument("--key", help="use this API key instead of approving a code in the browser")
    sub.add_parser("logout", help="forget Mason's API key")
    sub.add_parser("whoami", help="how mason reaches GitHub, and as whom")
    sp = sub.add_parser("market", help="plugins in the plugin index: list, install, sign")
    sp.add_argument("action", nargs="?", default="list", choices=("list", "install", "keygen", "sign", "verify"))
    sp.add_argument("name", nargs="?", help="plugin to install, or the index file to sign/verify")
    sp.add_argument("--index", help="index URL or file (default: MASON_PLUGIN_INDEX or the official one)")
    sp.add_argument("--key", help="sign: the private key (PEM); verify/list: trusted public keys, comma separated")
    sub.add_parser("plugins", help="installed plugins")
    sp = sub.add_parser("tui", parents=[common], help="full screen (needs: pipx inject mason-cli textual)")

    handlers: dict = {}
    for plugin in plugins.load():
        try:
            handlers.update(plugin.add_commands(sub) or {})
        except Exception as err:  # argparse conflicts and the like
            plugins.errors.append(f"{plugin.name}: {err}")
    return p, handlers


def show_config() -> None:
    mine = config.user_config()
    print(f"config file  {config.config_file()}{'' if config.config_file().exists() else ' (none yet)'}")
    cfg = config.load()
    print(f"defaults     {cfg.source or 'none (mason init <owner/repo or folder>)'}")
    print(f"orgs         {', '.join(cfg.orgs) or 'none'}")
    if cfg.repos:
        print(f"extra repos  {', '.join(cfg.repos)}")
    if mine.get("orgs"):
        print(f"your orgs    {', '.join(mine['orgs'])} (from your config)")
    print(f"groups       {len(cfg.groups)}")
    for g in cfg.groups:
        bits = [f"match {g.match}" if g.match else "the rest"]
        if g.reference:
            bits.append(f"from {g.reference}")
        if g.rulesets:
            bits.append(f"rulesets {g.rulesets}")
        if g.files:
            bits.append(f"files {g.files}")
        print(f"  {g.name}: {', '.join(bits)}")
    names = [p.name for p in plugins.load()]
    print(f"plugins      {', '.join(names) or 'none'}")
    for err in plugins.errors:
        print(f"  broken: {err}")


def init(args) -> None:
    values = config.user_config()
    values["defaults"] = args.defaults
    if args.ref:
        values["ref"] = args.ref
    source = config.Source(args.defaults, args.ref)
    if config.read_defaults(source)[1] is None:
        owners = args.org or ([] if source.local else [args.defaults.split("/")[0]])
        orgs = ", ".join(f'"{o}"' for o in owners)
        starter = config.STARTER.format(orgs=orgs)
        if source.local:
            source.local.mkdir(parents=True, exist_ok=True)
            (source.local / config.DEFAULTS_FILE).write_text(starter)
            print(f"wrote {source.local / config.DEFAULTS_FILE}")
        else:
            print(f"{args.defaults} has no {config.DEFAULTS_FILE} yet. Commit this as a start:\n")
            print(starter)
    path = config.save_user_config(values)
    print(f"mason now uses {source} (saved in {path})")


def list_repos(cfg, names: list[str]) -> None:
    for r in names:
        g = repos.group_of(cfg, r)
        alias = g.alias(r) if g else ""
        extra = f"  ({alias})" if g and alias != core.short(r) else ""
        print(f"{r:<45} {g.name if g else '-'}{extra}")
    print(f"{len(names)} repos")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["serve"]:  # the web app: mason serve [--host H] [--port P], mason serve --check
        from mason.server.app import main as server
        rest = argv[1:]
        server(["check", *(a for a in rest if a != "--check")] if "--check" in rest else ["serve", *rest])
        return
    parser, handlers = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra and args.cmd not in handlers:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    args._extra = extra  # plugin commands pass these on (options before their own arguments)
    core.set_dry_run(getattr(args, "dry_run", False))
    try:
        if args.cmd == "init":
            return init(args)
        if args.cmd == "config":
            return show_config()
        if args.cmd in ("login", "logout", "whoami"):
            from mason import login
            if args.cmd == "login":
                return login.login(args.url, "read" if args.read else "write", args.key)
            return login.logout() if args.cmd == "logout" else login.whoami()
        if args.cmd == "market":
            from mason import market
            return market.command(args.action, args.name, args.index, args.key)
        if args.cmd == "plugins":
            for p in plugins.load():
                print(f"{p.name:<16} {p.description}")
            for err in plugins.errors:
                print(f"broken: {err}")
            if not plugins.load() and not plugins.errors:
                print("No plugins installed (pipx inject mason-cli <package>).")
            return None
        if not core.transport().logged_in():
            sys.exit("Log in first: gh auth login (or mason login <Mason URL>)")
        if args.cmd in handlers:
            return handlers[args.cmd](args)
        cfg = config.load(getattr(args, "org", None))
        if args.cmd == "tui":
            try:
                from mason import tui
            except ImportError as err:
                sys.exit(f"The TUI needs Textual: pipx inject mason-cli textual. ({err})")
            return tui.run(cfg, dry_run=core.dry_run(), group=args.group)
        if args.cmd == "new":
            repo = args.repo if "/" in args.repo or len(cfg.orgs) != 1 else f"{cfg.orgs[0]}/{args.repo}"
            return defaults.create_repo(cfg, repo, args.group, args.public, args.description)
        if args.cmd in ("pr", "open"):
            repo, number = repos.resolve(cfg, args.target)
            if args.cmd == "open":
                return repos.open_page(repo, number, args.actions, args.releases)
            if not number:
                sys.exit("Name the PR: mason pr name#12")
            return repos.pr_detail(repo, number, args.diff, args.body)
        if args.cmd == "issue":
            return issue(cfg, args)
        if args.cmd == "bump":
            level = "patch"
            if len(args.repos) > 1 and versions.is_level(args.repos[-1]):
                level = args.repos.pop()
            names, _ = repos.pick_repos(cfg, args.repos, args.group)
            return versions.bump_all(cfg, names, level, args.auto_merge)
        if args.cmd == "release" and args.repos[:1] in (["new"], ["view"]):
            return release_new_or_view(cfg, args)
        if args.cmd == "release":
            names, _ = repos.pick_repos(cfg, args.repos, args.group)
            if args.list or not args.repos:
                return versions.summary(cfg, names)
            return versions.release_all(cfg, names, args.yes)
        names, pick = repos.pick_repos(cfg, args.repos, args.group)
        if pick and args.cmd != "merge":
            sys.exit("name#12 works with merge, pr and open")
        return run(cfg, args, names, pick)
    except RuntimeError as err:
        sys.exit(str(err))


def release_new_or_view(cfg, args) -> None:
    """mason release new name TAG [...] and mason release view name [TAG]."""
    from mason import releases
    action, rest = args.repos[0], args.repos[1:]
    if not rest:
        sys.exit(f"Name the repo: mason release {action} name" + (" v1.4.0" if action == "new" else " [tag]"))
    repo, _ = repos.resolve(cfg, rest[0])
    tag = rest[1] if len(rest) > 1 else ""
    if action == "view":
        return releases.view(cfg, repo, tag or None)
    if not tag:
        sys.exit("Name the tag: mason release new name v1.4.0")
    notes = sys.stdin.read() if args.notes == "-" else args.notes
    if not args.yes and not core.dry_run():
        what = "draft" if args.draft else "pre-release" if args.prerelease else "release"
        try:
            answer = input(f"Create the {what} {tag} of {repo}? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("left as it is")
            return None
    releases.create(repo, tag, args.title, notes, args.draft, args.prerelease, args.target, args.generate_notes)
    return None


def issue(cfg, args) -> None:
    repo, number = repos.resolve(cfg, args.target)
    if args.action == "new":
        body = sys.stdin.read() if args.body == "-" else args.body
        issues.create(repo, args.text, body, args.label, args.assignee)
        return
    if not number:
        sys.exit(f"Name the issue: mason issue {args.action} name#12")
    if args.action == "view":
        issues.view(repo, number)
    elif args.action == "comment":
        issues.comment(repo, number, sys.stdin.read() if args.text == "-" else args.text)
    else:
        issues.set_state(repo, number, args.action == "close", args.not_planned)


def run(cfg, args, names: list[str], pick: dict) -> None:
    if args.cmd == "repos":
        return list_repos(cfg, names)
    if args.cmd == "status":
        return repos.status(cfg, names)
    if args.cmd == "stats":
        from mason import stats
        return stats.show(names, args.days)
    if args.cmd == "prs":
        return repos.prs(cfg, names)
    if args.cmd == "runs":
        return repos.runs(names, args.limit, args.failed)
    if args.cmd == "rerun":
        if not args.repos:
            sys.exit("Name the repo: mason rerun hubra")
        return repos.rerun(names, args.run, args.yes)
    if args.cmd == "releases":
        return repos.releases(cfg, names, args.problems)
    if args.cmd == "packages":
        return packages.packages(cfg, names, args.problems)
    if args.cmd == "approve":
        return repos.approve(names, args.yes)
    if args.cmd == "security":
        return repos.security(names)
    if args.cmd == "doctor":
        return defaults.doctor_all(cfg, names, args.fix)
    if args.cmd == "workflows":
        return workflows.workflows(cfg, names, args.fix)
    if args.cmd == "issues":
        return issues.issues(names, args.state, args.label, args.assignee, args.mine, args.stale, args.limit)
    if args.cmd == "sync":
        ctx = defaults.sync_ctx(cfg, names, args.only)
        unknown = sorted({p for w in ctx["wants"].values() for p in w.get("unknown", [])})
        if unknown:
            sys.exit(f"not among the synced files: {', '.join(unknown)}")
        return core.for_each(names, defaults.sync, ctx)
    if args.cmd == "merge":
        ctx = {"pick": pick, "now": args.now, "ready": args.ready, "method": args.method}
        return core.for_each(names, repos.merge, ctx)
    return None


if __name__ == "__main__":
    main()
