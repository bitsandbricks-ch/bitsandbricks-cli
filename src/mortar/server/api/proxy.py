"""The GitHub proxy for `bnb` and `bnb tui` signed in to the operator (OperatorTransport).

The CLI sends GitHub REST and GraphQL calls here with its API key; the operator makes them with
the key owner's own GitHub token. So the CLI needs no gh and no GitHub token, and an API key can
be revoked in one place.

Not everything is passed on:
* reads (GET, GraphQL queries) only on the parts of the API bnb reads,
* writes only for the calls bnb and its plugins make, and only with a write key or a session,
* never: deleting repos, changing who has access, keys, hooks, or anything about the user's account.
"""

from __future__ import annotations

import re

from flask import Blueprint, Response, abort, g, request

from mortar.server import oauth
from mortar.server.auth import audit, can_write, need
from mortar.transport import HttpError

bp = Blueprint("proxy", __name__, url_prefix="/api/v1/github")

R = r"/repos/[\w.-]+/[\w.-]+"
READ = [re.compile(p) for p in (
    r"/user$", r"/user/repos$", r"/user/orgs$", r"/user/memberships/orgs/[\w.-]+$", r"/rate_limit$",
    r"/orgs/[\w.-]+(/repos|/packages|/packages/container/[^/]+/versions|/members/[\w.-]+)?$",
    r"/users/[\w.-]+(/repos|/packages|/packages/container/[^/]+/versions)?$",
    R + r"(/.*)?$",
)]
WRITE = {
    "PATCH": [R + "$", R + r"/pulls/\d+$", R + r"/rulesets/\d+$", R + r"/labels/[^/]+$"],
    "PUT": [R + r"/contents/.+$", R + r"/pulls/\d+/merge$", R + r"/pulls/\d+/update-branch$",
            R + r"/rulesets/\d+$", R + r"/automated-security-fixes$",
            R + r"/private-vulnerability-reporting$", R + r"/immutable-releases$", R + r"/environments/[^/]+$",
            R + r"/environments/[^/]+/secrets/[^/]+$", R + r"/topics$",
            R + r"/vulnerability-alerts$", R + r"/actions/permissions(/workflow)?$"],
    "POST": [R + r"/labels$", R + r"/git/refs$", R + r"/pulls$", R + r"/rulesets$", R + r"/actions/runs/\d+/approve$",
             R + r"/actions/runs/\d+/pending_deployments$", R + r"/actions/runs/\d+/(rerun(-failed-jobs)?|cancel)$",
             R + r"/generate$", r"/user/repos$", r"/orgs/[\w.-]+/repos$", R + r"/releases$", R + r"/git/tags$",
             R + r"/issues/\d+/comments$", R + r"/dispatches$", R + r"/actions/workflows/[^/]+/dispatches$"],
    "DELETE": [R + r"/git/refs/heads/.+$", R + r"/automated-security-fixes$", R + r"/private-vulnerability-reporting$",
               R + r"/immutable-releases$", R + r"/rulesets/\d+$", R + r"/vulnerability-alerts$"],
}
WRITE_RE = {m: [re.compile(p) for p in ps] for m, ps in WRITE.items()}
NEVER = re.compile(r"/(collaborators|invitations|keys|hooks|actions/secrets|actions/variables|transfer|"
                   r"teams|members|interaction-limits)(/|$)|/user/(emails|keys|gpg_keys|ssh_signing_keys)")
MUTATIONS = {"markPullRequestReadyForReview", "enablePullRequestAutoMerge", "disablePullRequestAutoMerge",
             "mergePullRequest", "convertPullRequestToDraft"}
OPERATION = re.compile(r"^\s*(?:#[^\n]*\n\s*)*(query|mutation|subscription|\{)", re.S)
MUTATION_FIELD = re.compile(r"(\w+)\s*\(\s*input\s*:")


def transport():
    t = oauth.github(g.user)
    if t is None:
        abort(409, "connect GitHub first (Settings › Accounts in the operator)")
    return t


def allowed(method: str, path: str) -> bool:
    if NEVER.search(path):
        return False
    if method in ("GET", "HEAD"):
        return any(p.match(path) for p in READ)
    return any(p.match(path) for p in WRITE_RE.get(method, []))


@bp.route("/rest/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@need("read")
def rest(path: str):
    path = "/" + path
    if ".." in path or "//" in path:
        abort(400, "bad path")
    if request.method != "GET" and not can_write():
        abort(403, "this API key is read-only")
    if not allowed(request.method, path):
        abort(403, f"the operator doesn't pass {request.method} {path} on")
    t = transport()
    url = t.api_url + path + (("?" + request.query_string.decode()) if request.query_string else "")
    accept = request.headers.get("Accept") or "application/vnd.github+json"
    if accept not in ("application/vnd.github+json", "application/json", "application/octet-stream",
                      "application/vnd.github.diff"):
        accept = "application/vnd.github+json"
    data = request.get_data() if request.method in ("POST", "PUT", "PATCH", "DELETE") else None
    try:
        status, headers, raw = t.request(request.method, url, data or None, accept=accept)
    except HttpError as err:
        return Response(err.body or b"{}", status=err.status, content_type="application/json")
    except RuntimeError as err:
        abort(502, str(err))
    if request.method != "GET":
        audit(f"proxy.{request.method.lower()}", path)
    ctype = headers.get("Content-Type") or headers.get("content-type") or "application/json"
    resp = Response(raw, status=status, content_type=ctype)
    for h in ("X-RateLimit-Remaining", "X-RateLimit-Limit", "X-RateLimit-Reset"):
        v = headers.get(h) or headers.get(h.lower())
        if v:
            resp.headers[h] = v
    return resp


@bp.post("/graphql")
@need("read")
def graphql():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "")
    m = OPERATION.match(query)
    if not m:
        abort(400, "not a GraphQL query")
    if m.group(1) == "subscription":
        abort(403, "no subscriptions")
    if m.group(1) == "mutation":
        if not can_write():
            abort(403, "this API key is read-only")
        names = set(MUTATION_FIELD.findall(query))
        if not names or names - MUTATIONS:
            abort(403, f"the operator doesn't pass these mutations on: {', '.join(sorted(names - MUTATIONS)) or '?'}")
        audit("proxy.graphql", ", ".join(sorted(names)))
    variables = payload.get("variables") or {}
    if not isinstance(variables, dict):
        abort(400, "variables must be an object")
    t = transport()
    import json
    try:
        status, _, raw = t.request("POST", t.graphql_url, json.dumps({"query": query, "variables": variables}).encode(),
                                   accept="application/json")
    except HttpError as err:
        return Response(err.body or b"{}", status=err.status, content_type="application/json")
    except RuntimeError as err:
        abort(502, str(err))
    return Response(raw, status=status, content_type="application/json")
