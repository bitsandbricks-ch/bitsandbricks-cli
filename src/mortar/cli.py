"""mortar (bnb): your GitHub repos, across orgs, from one terminal.

Repos are named as owner/name, as a bare name when only one repo has it, or by
their group alias (the group's prefix dropped). name#12 is a PR. No names: every
repo of your orgs. --org and --group narrow that down.

  bnb init your-org/your-defaults   point bnb at a defaults repo (or a local folder)
  bnb status | prs | runs           what's going on
  bnb stats [--days 30]             PRs merged, time to merge, CI success, releases
  bnb rerun name [--run ID]         re-run the failed jobs of a run
  bnb releases [--problems]         latest releases, missing assets, failed release runs
  bnb packages [--problems]         container images: newest tags, release images, image builds
  bnb bump name [patch|minor|1.4.0] a PR with the next version and its changelog section
  bnb release [names] [--yes]       tag v<version> where it is newer than the latest release
  bnb pr web#12 [--diff]            one PR in detail
  bnb merge [names] [--now]         auto-merge open PRs (drafts and conflicts stay)
  bnb approve                       runs waiting for you (environments, outside contributors)
  bnb security                      Dependabot, secret and code scanning alerts
  bnb doctor [names] [--fix]        settings, security, rulesets and files against the defaults
  bnb workflows [names] [--fix]     unpinned actions, write-all tokens, script injection; --fix: PRs that pin
  bnb sync [names]                  PRs that bring shared files in line
  bnb new owner/name [--group G]    a new repo, set up the way its group wants
  bnb tui                           all of it, full screen
  bnb login https://mortar          go through Mortar (the web app) with an API key: no gh needed
  bnb market [install NAME]         plugins from the signed plugin index
  mortar serve [--check]            run Mortar, the web app (the [server] extra)
"""

from __future__ import annotations

import argparse
import sys

from mortar import __version__, config, core, defaults, packages, plugins, repos, versions, workflows

ON_REPOS = ("status", "prs", "merge", "runs", "rerun", "releases", "packages", "approve", "security", "doctor", "sync",
            "repos", "stats", "workflows")


def common_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--org", action="append", metavar="ORG", help="only this org or user (repeatable)")
    common.add_argument("--group", help="only repos of this group")
    common.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    return common


def build_parser() -> tuple[argparse.ArgumentParser, dict]:
    p = argparse.ArgumentParser(prog="bnb", description=__doc__.split("\n\n", 1)[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--version", action="version", version=f"mortar {__version__}")
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
        "repos": "the repos bnb sees and their groups",
        "stats": "PRs merged, time to merge, CI success, releases over the last days",
        "workflows": "risky workflows: unpinned actions, write-all tokens, script injection",
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
    sp = sub.add_parser("pr", parents=[common], help="one PR in detail")
    sp.add_argument("target", help="name#12 or a PR URL")
    sp.add_argument("--diff", action="store_true")
    sp.add_argument("--body", action="store_true", help="the whole description")
    sp = sub.add_parser("open", parents=[common], help="print (and open or copy) a repo or PR link")
    sp.add_argument("target", help="name or name#12")
    sp.add_argument("--actions", action="store_true")
    sp.add_argument("--releases", action="store_true")
    sp = sub.add_parser("init", help="point bnb at a defaults repo (owner/repo or a local folder)")
    sp.add_argument("defaults", help="owner/repo or a folder with mortar.toml")
    sp.add_argument("--ref", help="branch or tag of the defaults repo")
    sp.add_argument("--org", action="append", metavar="ORG", help="orgs for a new mortar.toml")
    sp = sub.add_parser("new", parents=[common], help="create a repo the way its group wants it")
    sp.add_argument("repo", help="owner/name (name alone when bnb knows one org)")
    sp.add_argument("--public", action="store_true", help="public (default: private)")
    sp.add_argument("--description", default="")
    sub.add_parser("config", help="show where bnb looks and what it found")
    sp = sub.add_parser("login", help="sign in to Mortar (the web app) with an API key")
    sp.add_argument("url", help="Mortar's URL, e.g. https://mortar.example.org")
    sp.add_argument("--read", action="store_true", help="a read-only key")
    sp.add_argument("--key", help="use this API key instead of approving a code in the browser")
    sub.add_parser("logout", help="forget Mortar's API key")
    sub.add_parser("whoami", help="how bnb reaches GitHub, and as whom")
    sp = sub.add_parser("market", help="plugins in the plugin index: list, install, sign")
    sp.add_argument("action", nargs="?", default="list", choices=("list", "install", "keygen", "sign", "verify"))
    sp.add_argument("name", nargs="?", help="plugin to install, or the index file to sign/verify")
    sp.add_argument("--index", help="index URL or file (default: BNB_PLUGIN_INDEX or the official one)")
    sp.add_argument("--key", help="sign: the private key (PEM); verify/list: trusted public keys, comma separated")
    sub.add_parser("plugins", help="installed plugins")
    sp = sub.add_parser("tui", parents=[common], help="full screen (needs: pipx inject mortar-cli textual)")

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
    print(f"defaults     {cfg.source or 'none (bnb init <owner/repo or folder>)'}")
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
    print(f"bnb now uses {source} (saved in {path})")


def list_repos(cfg, names: list[str]) -> None:
    for r in names:
        g = repos.group_of(cfg, r)
        alias = g.alias(r) if g else ""
        extra = f"  ({alias})" if g and alias != core.short(r) else ""
        print(f"{r:<45} {g.name if g else '-'}{extra}")
    print(f"{len(names)} repos")


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["serve"]:  # the web app: mortar serve [--host H] [--port P], mortar serve --check
        from mortar.server.app import main as server
        server(["check"] if "--check" in argv else ["serve", *argv[1:]])
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
            from mortar import login
            if args.cmd == "login":
                return login.login(args.url, "read" if args.read else "write", args.key)
            return login.logout() if args.cmd == "logout" else login.whoami()
        if args.cmd == "market":
            from mortar import market
            return market.command(args.action, args.name, args.index, args.key)
        if args.cmd == "plugins":
            for p in plugins.load():
                print(f"{p.name:<16} {p.description}")
            for err in plugins.errors:
                print(f"broken: {err}")
            if not plugins.load() and not plugins.errors:
                print("No plugins installed (pipx inject mortar-cli <package>).")
            return None
        if not core.transport().logged_in():
            sys.exit("Log in first: gh auth login (or bnb login <Mortar URL>)")
        if args.cmd in handlers:
            return handlers[args.cmd](args)
        cfg = config.load(getattr(args, "org", None))
        if args.cmd == "tui":
            try:
                from mortar import tui
            except ImportError as err:
                sys.exit(f"The TUI needs Textual: pipx inject mortar-cli textual. ({err})")
            return tui.run(cfg, dry_run=core.dry_run(), group=args.group)
        if args.cmd == "new":
            repo = args.repo if "/" in args.repo or len(cfg.orgs) != 1 else f"{cfg.orgs[0]}/{args.repo}"
            return defaults.create_repo(cfg, repo, args.group, args.public, args.description)
        if args.cmd in ("pr", "open"):
            repo, number = repos.resolve(cfg, args.target)
            if args.cmd == "open":
                return repos.open_page(repo, number, args.actions, args.releases)
            if not number:
                sys.exit("Name the PR: bnb pr name#12")
            return repos.pr_detail(repo, number, args.diff, args.body)
        if args.cmd == "bump":
            level = "patch"
            if len(args.repos) > 1 and versions.is_level(args.repos[-1]):
                level = args.repos.pop()
            names, _ = repos.pick_repos(cfg, args.repos, args.group)
            return versions.bump_all(cfg, names, level, args.auto_merge)
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


def run(cfg, args, names: list[str], pick: dict) -> None:
    if args.cmd == "repos":
        return list_repos(cfg, names)
    if args.cmd == "status":
        return repos.status(cfg, names)
    if args.cmd == "stats":
        from mortar import stats
        return stats.show(names, args.days)
    if args.cmd == "prs":
        return repos.prs(cfg, names)
    if args.cmd == "runs":
        return repos.runs(names, args.limit, args.failed)
    if args.cmd == "rerun":
        if not args.repos:
            sys.exit("Name the repo: bnb rerun hubra")
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
