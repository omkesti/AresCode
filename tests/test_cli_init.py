"""Tests for `arescode init` (TASKS 5.2): generate/refuse/overwrite the ARES.md template."""

from __future__ import annotations

from typer.testing import CliRunner

from arescode.main import app

runner = CliRunner()


def test_init_writes_ares_md(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    ares = tmp_path / "ARES.md"
    assert ares.is_file()
    assert "ARES.md" in ares.read_text()


def test_init_refuses_existing_without_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ARES.md").write_text("KEEP ME\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert (tmp_path / "ARES.md").read_text() == "KEEP ME\n"  # untouched


def test_init_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ARES.md").write_text("OLD CONTENT\n")
    result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 0
    assert "OLD CONTENT" not in (tmp_path / "ARES.md").read_text()
