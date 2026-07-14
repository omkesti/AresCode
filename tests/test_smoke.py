"""Phase 0 smoke test: the package imports and layered configuration behaves.

Covers the full precedence chain (defaults < global < project < CLI) so a regression in
``load_config`` fails loudly. See TASKS 0.3 / 0.5.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentcli import __version__
from agentcli.config import Config, load_config


def test_version_is_a_string():
    assert isinstance(__version__, str)
    assert __version__


def test_default_config_values():
    cfg = Config()
    assert cfg.model == "qwen2.5-coder:7b"
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.num_ctx == 16384
    assert cfg.temperature == 0.1
    assert cfg.max_steps == 25


def test_load_config_falls_back_to_defaults(tmp_path):
    cfg = load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
    assert cfg.model == "qwen2.5-coder:7b"
    assert cfg.num_ctx == 16384


def test_project_toml_overrides_global(tmp_path):
    (tmp_path / "global.toml").write_text('model = "from-global"\nnum_ctx = 2048\n')
    (tmp_path / ".agentcli.toml").write_text('model = "from-project"\n')
    cfg = load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
    assert cfg.model == "from-project"  # project layer wins over global
    assert cfg.num_ctx == 2048          # global value survives where project is silent


def test_cli_overrides_beat_files(tmp_path):
    (tmp_path / ".agentcli.toml").write_text('model = "from-project"\nnum_ctx = 2048\n')
    cfg = load_config(
        project_dir=tmp_path,
        global_config_path=tmp_path / "global.toml",
        overrides={"model": "from-cli", "num_ctx": 4096},
    )
    assert cfg.model == "from-cli"
    assert cfg.num_ctx == 4096


def test_none_overrides_are_ignored(tmp_path):
    (tmp_path / ".agentcli.toml").write_text('model = "from-project"\n')
    cfg = load_config(
        project_dir=tmp_path,
        global_config_path=tmp_path / "global.toml",
        overrides={"model": None, "num_ctx": None},
    )
    assert cfg.model == "from-project"  # an unset CLI flag must not clobber the file value


def test_unknown_config_key_is_rejected(tmp_path):
    (tmp_path / ".agentcli.toml").write_text("bogus_key = 1\n")
    with pytest.raises(ValidationError):
        load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
