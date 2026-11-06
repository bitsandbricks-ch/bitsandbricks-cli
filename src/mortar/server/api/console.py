"""The web console: type `mtr status`, `mtr doctor --fix web` ... and see what the CLI would print.

The command runs in the operator with your GitHub token and your settings; commands that only make
sense on your own machine (init, tui, open, config files) are refused, and so are plugin commands
that don't say they are safe for the web (a plugin lists those in `web_commands`).
"""

from __future__ import annotations

import shlex

from flask import Blueprint, abort, jsonify

from mortar import cli, config, core, plugins
from mortar.server.api.account import body
from mortar.server.auth import audit, can_write, need
from mortar.server.context import as_user, user_settings

bp = Blueprint("console", __name__, url_prefix="/api/v1")

READS = {"status", "prs", "pr", "runs", "releases", "packages", "security", "repos", "plugins", "stats", "workflows",
         "issues"}
WRITES = {"merge", "approve", "doctor", "sync", "new", "rerun", "bump", "release", "issue"}
# write unless --dry-run (doctor, workflows: unless no --fix; release: unless --list or no names; issue: view)
LOCAL = {"serve", "init", "tui", "open", "config", "login", "logout", "whoami", "market", "key"}


def web_commands() -> set[str]:
    out = set()
    for p in plugins.load():
        out |= set(getattr(p, "web_commands", ()) or ())
    return out


def is_write(argv: list[str]) -> bool:
    cmd = argv[0]
    if "--dry-run" in argv:
        return False
    if cmd in ("doctor", "workflows"):
        return "--fix" in argv
    if cmd == "issue":
        return len(argv) < 2 or argv[1] != "view"
    if cmd == "release":
        return "--list" not in argv and any(not a.startswith("-") for a in argv[1:])
    return cmd in WRITES or cmd in web_commands()


@bp.get("/console/commands")
@need("read")
def commands():
    plug = sorted(web_commands())
    return jsonify({"reads": sorted(READS), "writes": sorted(WRITES), "plugins": plug,
                    "help": core.capture(cli.main, ["--help"])})


@bp.post("/console")
@need("read")
def run():
    data = body()
    line = str(data.get("line") or "").strip()
    try:
        argv = shlex.split(line)
    except ValueError as err:
        abort(400, f"can't read that line: {err}")
    if argv and argv[0] in ("mtr", "mortar"):
        argv = argv[1:]
    if not argv:
        abort(400, "type a command, e.g. mtr status")
    cmd = argv[0]
    if cmd in ("help", "--help", "-h"):
        return jsonify({"text": core.capture(cli.main, ["--help"]), "write": False})
    if cmd in LOCAL:
        abort(400, f"`mtr {cmd}` works on your own machine, not in the operator (Settings has the same)")
    if cmd not in READS | WRITES | web_commands():
        abort(400, f"`mtr {cmd}` isn't available in the web console")
    write = is_write(argv)
    if write and not can_write():
        abort(403, "this API key is read-only: add --dry-run")
    if cmd in ("approve", "rerun", "release") and write and "--yes" not in argv:
        argv.append("--yes")  # nobody can answer y/N here; the console asked before sending
    s = user_settings(__import__("flask").g.user)
    mine = {k: s[k] for k in ("defaults", "ref", "orgs", "repos", "exclude") if s.get(k)}
    with as_user():
        token = config.OVERRIDE.set(mine)
        try:
            text = core.capture(cli.main, argv)
        except core.RateLimited as err:
            abort(429, str(err))
        finally:
            config.OVERRIDE.reset(token)
    if write:
        audit(f"console.{cmd}", line[:300], dry_run=False, detail=text)
    return jsonify({"text": text, "write": write})
