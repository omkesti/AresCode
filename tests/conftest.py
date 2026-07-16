"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from arescode import config as config_module


@pytest.fixture(autouse=True)
def _isolate_last_model(tmp_path, monkeypatch):
    """Redirect the remembered-model file (D13) to a throwaway path for every test.

    Without this, ``load_config`` / ``read_last_model`` / ``save_last_model`` would read and write
    the developer's real ``~/.arescode/last_model``, so a local ``/model`` switch could leak into
    (or be clobbered by) the test suite. Each test gets its own nonexistent path under ``tmp_path``.
    """
    monkeypatch.setattr(config_module, "LAST_MODEL_PATH", tmp_path / "last_model")
