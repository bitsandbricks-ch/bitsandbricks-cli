"""Build the web UI into the package when a wheel or sdist is built.

`pipx install "mason-cli[server] @ git+..."` builds a wheel from the repository, and the built UI
(src/mason/server/static) isn't in git. This hook runs `npm ci && npm run build` in web/ so the
wheel carries it. It does nothing when the UI is already built (the Docker image builds it first),
for editable installs (run npm yourself while developing), or with MASON_SKIP_WEB_BUILD=1. Without
Node.js the package still builds, without the UI; `mason serve` then says how to build it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebBuildHook(BuildHookInterface):
    PLUGIN_NAME = "mason-web"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        web, index = root / "web", root / "src" / "mason" / "server" / "static" / "index.html"
        if version == "editable" or os.environ.get("MASON_SKIP_WEB_BUILD") or index.is_file():
            return
        if not (web / "package.json").is_file():
            return  # a wheel built from an sdist that has no web/ sources
        npm = shutil.which("npm")
        if not npm:
            print("mason: npm not found, so this build has no web UI. Install Node.js and reinstall,"
                  " or run: cd web && npm ci && npm run build", file=sys.stderr)
            return
        for step in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
            subprocess.run([npm, *step], cwd=web, check=True)
