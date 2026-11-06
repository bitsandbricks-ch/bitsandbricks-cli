"""Where Mortar's built web UI lives, and what to say when it's missing."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parent / "static"

BUILD_HINT = (
    "Mortar's web UI isn't built, so the browser gets only this message.\n"
    "From a clone of https://github.com/bitsandbricks-ch/mortar-cli, build it with:\n"
    "    cd web && npm ci && npm run build\n"
    "or reinstall with Node.js (npm) on the PATH, which builds it into the package:\n"
    '    pipx install --force "mortar-cli[tui,server] @ git+https://github.com/bitsandbricks-ch/mortar-cli"'
)


def built(static: Path = STATIC) -> bool:
    return (static / "index.html").is_file()
