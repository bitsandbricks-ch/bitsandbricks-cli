"""Mason's server: mason as a web app for many users, each signed in with GitHub and/or GitLab.

    mason serve                  run it (gunicorn in production: see the Dockerfile)
    mason serve --check          show what the settings allow and what is missing

Every setting comes from the environment (docs/mason.md). The web UI is a single page app in
static/ (built from web/ with Vite); /api/v1 is the JSON API the UI, the CLI and the TUI share.
"""

from __future__ import annotations

import sys

from flask import Flask, abort, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from mason import core, transport
from mason.server import auth, models, security, webui
from mason.server import settings as S
from mason.server.context import NoTransport

STATIC = webui.STATIC


def create_app(op: S.Settings | None = None) -> Flask:
    op = op or S.load()
    problems = op.problems()
    if problems:
        raise SystemExit("Mason won't start:\n  - " + "\n  - ".join(problems))
    app = Flask(__name__, static_folder=None)
    app.config.update(OP=op, VAULT=security.Vault(op.secret_key), MAX_CONTENT_LENGTH=1024 * 1024,
                      PROBLEMS=problems, JSON_SORT_KEYS=False)
    models.init(op.database)
    core.install_context_stdout()
    # The operator acts only with each user's own token: never with gh or a token of the server's.
    transport.set_default(NoTransport("sign in first"))
    if op.trusted_proxies:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=op.trusted_proxies, x_proto=op.trusted_proxies,
                                x_host=op.trusted_proxies)

    @app.before_request
    def _who():
        auth.resolve()
        if request.path.startswith("/api/"):
            auth.check_csrf()

    @app.teardown_appcontext
    def _done(_exc):
        models.db.remove()

    @app.after_request
    def _headers(resp):
        for k, v in security.headers(op.secure_cookies).items():
            resp.headers.setdefault(k, v)
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        elif request.path.startswith("/assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @app.errorhandler(HTTPException)
    def _http_error(err):
        if request.path.startswith("/api/"):
            return auth.error_json(err)
        return err

    @app.errorhandler(core.RateLimited)
    def _rate(err):
        return jsonify({"error": str(err), "reset": err.reset}), 429

    from mason.server.api import account, console, defaults, metrics, plugins, proxy, repos
    for mod in (account, repos, proxy, console, defaults, metrics, plugins):
        app.register_blueprint(mod.bp)

    @app.get("/api/v1/health")
    def _health():
        return jsonify({"ok": True, "version": _version(), "problems": problems if g.get("user") and
                        g.user.is_admin else len(problems)})

    @app.get("/api/<path:_rest>")
    def _no_api(_rest):
        abort(404, "no such API")

    @app.get("/")
    @app.get("/<path:path>")
    def _spa(path: str = ""):
        if path and (STATIC / path).is_file() and ".." not in path:
            return send_from_directory(STATIC, path)
        if not webui.built(STATIC):
            return (webui.BUILD_HINT, 503,
                    {"Content-Type": "text/plain; charset=utf-8"})
        resp = send_from_directory(STATIC, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    return app


def _version() -> str:
    from mason import __version__
    return __version__


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(prog="mason-server", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="run the operator (development server; use gunicorn in production)")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=S.DEFAULT_PORT,
                    help="port to listen on (default 8080); without MASON_URL, Mason's URL is http://localhost:PORT")
    cp = sub.add_parser("check", help="what the settings allow, and what is missing")
    cp.add_argument("--port", type=int, default=S.DEFAULT_PORT, help="the port `serve` will use")
    cp.add_argument("--host", help=argparse.SUPPRESS)  # so `mason serve --check` takes serve's options
    args = p.parse_args(argv)
    op = S.load(args.port)
    if args.cmd == "check":
        print(f"url          {op.url or '(MASON_URL is not set)'}")
        print(f"data         {op.data}")
        print(f"database     {op.database.split('@')[-1]}")
        for p in (op.github, op.gitlab):
            how = {"redirect": f"on, redirect to {op.url}/api/v1/auth/{p.name}/callback",
                   "device": f"on, device flow (client {p.client_id})"}.get(p.method, "off")
            print(f"{p.name:<13}{how} ({p.web})")
        who = ("anyone with an account" if op.open_sign_in
               else f"users {sorted(op.allow_users) or '-'}, orgs {sorted(op.allow_orgs) or '-'}")
        print(f"sign-in      {who}; admins {sorted(op.admins) or '-'}")
        print(f"plugins      index {op.plugin_index}, install from the web {'on' if op.plugin_install else 'off'}")
        print(f"web ui       {'built' if webui.built(STATIC) else 'NOT BUILT: cd web && npm ci && npm run build'}")
        for problem in op.problems():
            print(f"PROBLEM      {problem}")
        sys.exit(1 if op.problems() else 0)
    app = create_app(op)
    if not webui.built(STATIC):
        print("\n" + webui.BUILD_HINT + "\n", file=sys.stderr)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
