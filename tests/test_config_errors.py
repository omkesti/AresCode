"""Config error paths (TASKS 6.3): a bad TOML file or an out-of-range value must surface as a
clean ConfigError (and a clean CLI exit), never a raw tomllib/pydantic traceback."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from arescode import config as config_module
from arescode.config import ConfigError, load_config
from arescode.main import app

runner = CliRunner()


def test_malformed_toml_raises_config_error_naming_the_file(tmp_path):
    (tmp_path / ".arescode.toml").write_text("model = = broken\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
    assert ".arescode.toml" in str(excinfo.value)
    assert "invalid TOML" in str(excinfo.value)


def test_invalid_value_raises_config_error_naming_the_field(tmp_path):
    (tmp_path / ".arescode.toml").write_text("num_ctx = -5\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(project_dir=tmp_path, global_config_path=tmp_path / "global.toml")
    assert "num_ctx" in str(excinfo.value)  # the offending field is named, no traceback


def test_cli_exits_cleanly_on_malformed_config(tmp_path, monkeypatch):
    # The whole point of ConfigError: `arescode` exits 1 with a message instead of a traceback.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_module, "GLOBAL_CONFIG_PATH", tmp_path / "no-global.toml")
    (tmp_path / ".arescode.toml").write_text("num_ctx = -5\n")

    result = runner.invoke(app, [])

    assert result.exit_code == 1
    # The failure is a clean exit, not a leaked ConfigError bubbling out of the callback.
    assert not isinstance(result.exception, ConfigError)
