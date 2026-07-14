"""Phase 0 smoke test: the package imports and layered configuration behaves.

Covers the full precedence chain (defaults < global < project < CLI) so a regression in
``load_config`` fails loudly. See TASKS 0.3 / 0.5.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from arescode import __version__
from arescode import config as config_module
from arescode.config import Config, load_config, migrate_legacy_paths


def test_version_is_a_string():
    assert isinstance(__version__, str)
    assert __version__


def test_default_config_values():
    cfg = Config()
    assert cfg.model == "qwen2.5-coder:14b-instruct"
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.num_ctx == 16384
    assert cfg.temperature == 0.1
    assert cfg.max_steps == 25


def test_load_config_falls_back_to_defaults(tmp_path):
    cfg = load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
    assert cfg.model == "qwen2.5-coder:14b-instruct"
    assert cfg.num_ctx == 16384


def test_project_toml_overrides_global(tmp_path):
    (tmp_path / "global.toml").write_text('model = "from-global"\nnum_ctx = 2048\n')
    (tmp_path / ".arescode.toml").write_text('model = "from-project"\n')
    cfg = load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
    assert cfg.model == "from-project"  # project layer wins over global
    assert cfg.num_ctx == 2048          # global value survives where project is silent


def test_cli_overrides_beat_files(tmp_path):
    (tmp_path / ".arescode.toml").write_text('model = "from-project"\nnum_ctx = 2048\n')
    cfg = load_config(
        project_dir=tmp_path,
        global_config_path=tmp_path / "global.toml",
        overrides={"model": "from-cli", "num_ctx": 4096},
    )
    assert cfg.model == "from-cli"
    assert cfg.num_ctx == 4096


def test_none_overrides_are_ignored(tmp_path):
    (tmp_path / ".arescode.toml").write_text('model = "from-project"\n')
    cfg = load_config(
        project_dir=tmp_path,
        global_config_path=tmp_path / "global.toml",
        overrides={"model": None, "num_ctx": None},
    )
    assert cfg.model == "from-project"  # an unset CLI flag must not clobber the file value


def test_unknown_config_key_is_rejected(tmp_path):
    (tmp_path / ".arescode.toml").write_text("bogus_key = 1\n")
    with pytest.raises(ValidationError):
        load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")


def test_migrate_legacy_project_paths(tmp_path):
    """Old per-project .agentcli.toml and .agentcli/sessions are copied to the arescode paths."""
    (tmp_path / ".agentcli.toml").write_text('model = "from-legacy"\n')
    legacy_sessions = tmp_path / ".agentcli" / "sessions"
    legacy_sessions.mkdir(parents=True)
    (legacy_sessions / "20260101-000000.json").write_text("{}")

    migrated = migrate_legacy_paths(project_dir=tmp_path)

    assert (tmp_path / ".arescode.toml").read_text() == 'model = "from-legacy"\n'
    assert (tmp_path / ".arescode" / "sessions" / "20260101-000000.json").is_file()
    assert (tmp_path / ".agentcli.toml").exists()  # copy, not move — old build still works
    assert len(migrated) == 2


def test_migrate_legacy_global_dir(tmp_path, monkeypatch):
    """A ~/.agentcli directory migrates to ~/.arescode when the latter does not yet exist."""
    legacy_global = tmp_path / ".agentcli"
    legacy_global.mkdir()
    (legacy_global / "config.toml").write_text('model = "from-legacy-global"\n')
    new_global = tmp_path / ".arescode"
    monkeypatch.setattr(config_module, "LEGACY_GLOBAL_DIR", legacy_global)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_PATH", new_global / "config.toml")

    migrated = migrate_legacy_paths(project_dir=tmp_path)

    assert (new_global / "config.toml").read_text() == 'model = "from-legacy-global"\n'
    assert any("agentcli" in m for m in migrated)


def test_migrate_is_noop_when_new_paths_exist(tmp_path):
    """Migration never clobbers current data: existing arescode paths are left untouched."""
    (tmp_path / ".agentcli.toml").write_text('model = "old"\n')
    (tmp_path / ".arescode.toml").write_text('model = "current"\n')

    migrated = migrate_legacy_paths(project_dir=tmp_path)

    assert (tmp_path / ".arescode.toml").read_text() == 'model = "current"\n'
    assert migrated == []
