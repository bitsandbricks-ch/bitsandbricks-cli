"""Stats over the last N days for GitHub and GitLab (mtr stats as charts), and the operator's own numbers."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, request

from mortar import core, stats
from mortar.server import gitlab as GL
from mortar.server.api.repos import all_repos, names_arg
from mortar.server.auth import need
from mortar.server.context import as_user, load_config

bp = Blueprint("metrics", __name__, url_prefix="/api/v1")


@bp.get("/metrics")
@need("read")
def metrics():
    days = max(1, min(request.args.get("days", 30, type=int), 365))
    with as_user() as (gh, gl):
        cfg = load_config()
        gh_r, gl_r, _ = all_repos(cfg, gh, gl, names_arg(), request.args.get("group"))
        try:
            rows = stats.github(gh_r, days) if gh_r else []
            rows += GL.stats(gl, gl_r, days) if gl and gl_r else []
        except core.RateLimited as err:
            abort(429, str(err))
        by_group: dict[str, list] = {}
        for r in rows:
            g = cfg.group_of(r["repo"])
            by_group.setdefault(g.name if g else "-", []).append(r)
        return jsonify({"days": days, "repos": rows, "totals": stats.totals(rows, days),
                        "groups": {k: stats.totals(v, days) for k, v in by_group.items()},
                        "cache": {"github": getattr(gh, "stats", {}),
                                  "gitlab": getattr(gl, "stats", {}) if gl else {}}})
