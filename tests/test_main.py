"""CLI entry-point tests (TASKS 6.4): --version short-circuits before launching the REPL."""

from __future__ import annotations

from typer.testing import CliRunner

from arescode import __version__
from arescode.main import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert "arescode" in result.stdout


def test_version_flag_does_not_launch_repl(monkeypatch):
    # --version is eager: it must exit before the REPL (or any config load) runs.
    def _boom(*args, **kwargs):  # pragma: no cover - should never be reached
        raise AssertionError("--version must not launch the REPL")

    monkeypatch.setattr("arescode.config.load_config", _boom)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
