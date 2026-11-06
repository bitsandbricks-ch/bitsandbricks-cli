"""Mason's secret key: MASON_SECRET_KEY, or one made on first run in the data folder and kept there."""

from __future__ import annotations

import os
import stat
import sys

import pytest

if sys.version_info < (3, 10):  # pragma: no cover
    pytest.skip("the operator needs Python 3.10+", allow_module_level=True)


def test_made_once_in_the_data_folder(monkeypatch, tmp_path):
    from mason.server import settings as S

    monkeypatch.delenv("MASON_SECRET_KEY", raising=False)
    data = tmp_path / "data"
    key = S.secret_key(data)
    path = data / "secret.key"
    assert len(key) >= 40 and path.read_text() == key
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert S.secret_key(data) == key  # kept, not made again
    assert S.secret_key(tmp_path / "other") != key  # every install gets its own


def test_env_wins(monkeypatch, tmp_path):
    from mason.server import settings as S

    monkeypatch.setenv("MASON_SECRET_KEY", "from-the-env")
    assert S.secret_key(tmp_path) == "from-the-env"
    assert not (tmp_path / "secret.key").exists()
