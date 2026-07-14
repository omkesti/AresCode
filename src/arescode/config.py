"""Layered configuration: a pydantic schema plus TOML file loading.

Precedence, lowest to highest (TASKS 0.3, context.md §4.7):

    built-in defaults
      -> ~/.arescode/config.toml   (global)
      -> ./.arescode.toml          (per project)
      -> CLI flag overrides

``load_config`` merges these layers and validates the result. Unknown keys are
rejected (``extra="forbid"``) so a typo in a TOML file fails loudly instead of
being silently ignored.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GLOBAL_CONFIG_PATH = Path.home() / ".arescode" / "config.toml"
PROJECT_CONFIG_NAME = ".arescode.toml"


class Config(BaseModel):
    """Validated runtime configuration for a session."""

    # protected_namespaces=() silences pydantic's warning about the ``model`` field name.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str = Field(default="qwen2.5-coder:7b", description="Ollama model tag to run.")
    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible endpoint base URL.",
    )
    num_ctx: int = Field(default=16384, gt=0, description="Context window size in tokens.")
    temperature: float = Field(default=0.1, ge=0.0, description="Sampling temperature.")
    max_steps: int = Field(default=25, gt=0, description="Hard cap on agent loop steps per turn.")
    request_timeout: float = Field(
        default=120.0, gt=0, description="Per-model-call timeout in seconds."
    )
    bash_timeout: float = Field(
        default=60.0, gt=0, description="Per-shell-command timeout in seconds."
    )


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file into a dict, returning ``{}`` if it does not exist."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(
    *,
    project_dir: Path | None = None,
    overrides: dict[str, Any] | None = None,
    global_config_path: Path | None = None,
) -> Config:
    """Build a :class:`Config` by merging every configuration layer.

    Args:
        project_dir: Directory to look for ``.arescode.toml`` in (defaults to cwd).
        overrides: CLI-flag values; keys whose value is ``None`` are ignored so an
            unset flag never clobbers a file/default value.
        global_config_path: Location of the global config (overridable for tests).
    """
    project_dir = project_dir or Path.cwd()
    global_config_path = global_config_path or GLOBAL_CONFIG_PATH

    merged: dict[str, Any] = {}
    merged.update(_read_toml(global_config_path))
    merged.update(_read_toml(project_dir / PROJECT_CONFIG_NAME))
    if overrides:
        merged.update({key: value for key, value in overrides.items() if value is not None})

    return Config(**merged)
