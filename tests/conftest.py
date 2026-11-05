from __future__ import annotations

import os
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="session")
def env(tmp_path_factory):
    bin_dir = tmp_path_factory.mktemp("bin")
    (bin_dir / "gh").symlink_to(HERE / "fake_gh")
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("BNB_", "SSH_"))}
    return {
        **clean,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_LOG": str(bin_dir / "calls.log"),
        "XDG_CONFIG_HOME": str(bin_dir / "config"),
        "BNB_DEFAULTS": "acme/defaults",
        "BNB_OPEN": "print",
        "BNB_PLUGINS": "demo",  # only the tests' own plugin, whatever else is installed
    }
