"""The built web UI: `mtr serve` says how to build it when it's missing, and the build hook builds it."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("flask")
pytest.importorskip("sqlalchemy")
if sys.version_info < (3, 10):  # pragma: no cover
    pytest.skip("the operator needs Python 3.10+", allow_module_level=True)


@pytest.fixture()
def served(monkeypatch, tmp_path):
    """Run `mtr serve` and `mtr serve --check` with the UI in tmp_path/static, without starting a server."""
    from mortar.server import app as A

    monkeypatch.delenv("MTR_URL", raising=False)
    monkeypatch.setenv("MTR_DATA", str(tmp_path))
    static = tmp_path / "static"
    static.mkdir()
    monkeypatch.setattr(A, "STATIC", static)

    class FakeApp:
        def run(self, **kw):
            pass

    monkeypatch.setattr(A, "create_app", lambda op: FakeApp())
    return static


def test_serve_says_how_to_build_a_missing_ui(served, capsys):
    from mortar import cli

    cli.main(["serve"])
    err = capsys.readouterr().err
    assert "web UI isn't built" in err
    assert "cd web && npm ci && npm run build" in err
    with pytest.raises(SystemExit):
        cli.main(["serve", "--check"])
    assert "web ui       NOT BUILT: cd web && npm ci && npm run build" in capsys.readouterr().out


def test_serve_is_quiet_when_the_ui_is_built(served, capsys):
    from mortar import cli

    (served / "index.html").write_text("<!doctype html>")
    cli.main(["serve"])
    assert "isn't built" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["serve", "--check"])
    assert "web ui       built" in capsys.readouterr().out


def test_browser_gets_the_build_hint(monkeypatch, tmp_path):
    from mortar.server import app as A
    from mortar.server import settings as S
    from mortar.server import webui

    monkeypatch.setattr(A, "STATIC", tmp_path)
    settings = S.Settings(url="http://localhost:8080", data=tmp_path, database=f"sqlite:///{tmp_path / 'op.db'}",
                          secret_key="k" * 40,
                          github=S.Provider("github", "id", "", "https://github.com", "https://api.github.com", "repo"),
                          gitlab=S.Provider("gitlab", "", "", "https://gitlab.com", "https://gitlab.com/api/v4", "api"))
    resp = A.create_app(settings).test_client().get("/")
    assert resp.status_code == 503
    assert resp.get_data(as_text=True) == webui.BUILD_HINT
