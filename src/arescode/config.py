"""Layered configuration: a pydantic schema plus TOML file loading.

Precedence, lowest to highest (TASKS 0.3, context.md §4.7):

    built-in defaults
      -> remembered model          (~/.arescode/last_model, D13)
      -> ~/.arescode/config.toml   (global)
      -> ./.arescode.toml          (per project)
      -> CLI flag overrides

``load_config`` merges these layers and validates the result. Unknown keys are
rejected (``extra="forbid"``) so a typo in a TOML file fails loudly instead of
being silently ignored.

The *remembered model* (D13) is machine-managed state written by a ``/model`` switch: it seeds the
``model`` key just above the built-in default so a switch sticks across launches, while an explicit
``model`` in a config file or a ``--model`` flag still overrides it. See :func:`read_last_model`.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

GLOBAL_CONFIG_PATH = Path.home() / ".arescode" / "config.toml"
PROJECT_CONFIG_NAME = ".arescode.toml"

# Machine-managed "last model the user switched to" (D13). Kept separate from the hand-edited
# config.toml so remembering a choice never rewrites a user's comments/formatting.
LAST_MODEL_PATH = Path.home() / ".arescode" / "last_model"

# Pre-rename ("agent-cli") locations, kept only so the one-time migration below can find them.
LEGACY_GLOBAL_DIR = Path.home() / ".agentcli"
LEGACY_PROJECT_CONFIG_NAME = ".agentcli.toml"
LEGACY_PROJECT_STATE_DIR = ".agentcli"  # old per-project dir that held sessions/


class ModelSettings(BaseModel):
    """Per-model overrides for a ``[models."<tag>"]`` section (D12).

    Any field left unset falls back to the top-level default of the same name, so a section may
    tune just ``num_ctx`` (the usual case — a big 14B needs a smaller KV cache on low VRAM) and
    inherit the rest. Unknown keys are rejected so a typo fails loudly.
    """

    model_config = ConfigDict(extra="forbid")

    num_ctx: int | None = Field(default=None, gt=0, description="Context window for this model.")
    temperature: float | None = Field(default=None, ge=0.0, description="Sampling temperature.")


class Config(BaseModel):
    """Validated runtime configuration for a session."""

    # protected_namespaces=() silences pydantic's warning about the ``model`` field name.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    # The out-of-the-box default is the light 7B tag so a first run works on a low-VRAM box (D13).
    # The stronger `qwen2.5-coder:14b-instruct` (D11) is one `/model` switch away, and that choice
    # is remembered (LAST_MODEL_PATH) so it becomes the default from the next launch on. Either way
    # the harness is unchanged — robustness is not a 7B workaround.
    model: str = Field(default="qwen2.5-coder:7b", description="Ollama model tag to run.")
    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible endpoint base URL.",
    )
    # 16384 fits the 7B default (~4.7GB Q4) comfortably. The KV cache grows with num_ctx, so if you
    # switch to a 14B on a <12GB GPU give it a smaller window via [models."<tag>"] (or drop this).
    num_ctx: int = Field(default=16384, gt=0, description="Context window size in tokens.")
    temperature: float = Field(default=0.1, ge=0.0, description="Sampling temperature.")
    max_steps: int = Field(default=25, gt=0, description="Hard cap on agent loop steps per turn.")
    allow_commands: list[str] = Field(
        default_factory=list,
        description="bash first-tokens to auto-approve without asking (persistent allowlist).",
    )
    allow_paths: list[str] = Field(
        default_factory=list,
        description="file paths to auto-approve for write/edit (persistent allowlist).",
    )
    request_timeout: float = Field(
        default=120.0, gt=0, description="Per-model-call timeout in seconds."
    )
    bash_timeout: float = Field(
        default=60.0, gt=0, description="Per-shell-command timeout in seconds."
    )
    # Per-model overrides, keyed by Ollama tag (e.g. models."qwen2.5-coder:14b-instruct").
    # A model with no section here uses the top-level num_ctx/temperature as its defaults (D12).
    models: dict[str, ModelSettings] = Field(
        default_factory=dict,
        description="Per-model num_ctx/temperature overrides; unknown models use the defaults.",
    )

    def settings_for(self, model: str) -> tuple[int, float]:
        """Resolve (num_ctx, temperature) for ``model``: its section, else top-level defaults."""
        section = self.models.get(model)
        num_ctx = section.num_ctx if section and section.num_ctx is not None else self.num_ctx
        temp = (
            section.temperature
            if section and section.temperature is not None
            else self.temperature
        )
        return num_ctx, temp

    def for_model(self, model: str) -> Config:
        """A copy of this config with ``model`` active and its per-model settings applied."""
        num_ctx, temperature = self.settings_for(model)
        return self.model_copy(
            update={"model": model, "num_ctx": num_ctx, "temperature": temperature}
        )


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file into a dict, returning ``{}`` if it does not exist."""
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def read_last_model(path: Path | None = None) -> str | None:
    """Return the model last selected via ``/model``, or ``None`` if never set (D13).

    This is machine-managed state — one bare model tag on a single line. It overrides only the
    built-in default (see :func:`load_config`); an explicit ``model`` in a config file or on the
    CLI still wins. A missing/blank file (or any read error) reads as "not set".
    """
    path = path or LAST_MODEL_PATH
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_last_model(model: str, path: Path | None = None) -> None:
    """Persist ``model`` as the remembered default for the next launch (D13)."""
    path = path or LAST_MODEL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.strip() + "\n", encoding="utf-8")


def load_config(
    *,
    project_dir: Path | None = None,
    overrides: dict[str, Any] | None = None,
    global_config_path: Path | None = None,
    last_model_path: Path | None = None,
) -> Config:
    """Build a :class:`Config` by merging every configuration layer.

    Args:
        project_dir: Directory to look for ``.arescode.toml`` in (defaults to cwd).
        overrides: CLI-flag values; keys whose value is ``None`` are ignored so an
            unset flag never clobbers a file/default value.
        global_config_path: Location of the global config (overridable for tests).
        last_model_path: Location of the remembered-model file (overridable for tests).
    """
    project_dir = project_dir or Path.cwd()
    global_config_path = global_config_path or GLOBAL_CONFIG_PATH

    merged: dict[str, Any] = {}
    # The remembered model (D13) overrides only the built-in default: seed it here so the config
    # files and CLI overrides below still take precedence when they set ``model`` explicitly.
    remembered = read_last_model(last_model_path)
    if remembered:
        merged["model"] = remembered
    merged.update(_read_toml(global_config_path))
    merged.update(_read_toml(project_dir / PROJECT_CONFIG_NAME))
    if overrides:
        merged.update({key: value for key, value in overrides.items() if value is not None})

    return Config(**merged)


def migrate_legacy_paths(project_dir: Path | None = None) -> list[str]:
    """Copy pre-rename ``agent-cli`` config/sessions to their ``arescode`` locations, once.

    Each move fires only when the old path exists and the new one does not, so it never
    clobbers current data; we *copy* rather than move so an older build still finds its files.
    Returns a short human-readable description of each path migrated (empty when nothing to do),
    which the caller prints as a single info line on startup.
    """
    project_dir = project_dir or Path.cwd()
    new_global_dir = GLOBAL_CONFIG_PATH.parent
    migrated: list[str] = []

    # Global config/state directory: ~/.agentcli -> ~/.arescode
    if LEGACY_GLOBAL_DIR.is_dir() and not new_global_dir.exists():
        shutil.copytree(LEGACY_GLOBAL_DIR, new_global_dir)
        migrated.append(f"{LEGACY_GLOBAL_DIR} -> {new_global_dir}")

    # Per-project config file: ./.agentcli.toml -> ./.arescode.toml
    legacy_cfg = project_dir / LEGACY_PROJECT_CONFIG_NAME
    new_cfg = project_dir / PROJECT_CONFIG_NAME
    if legacy_cfg.is_file() and not new_cfg.exists():
        shutil.copy2(legacy_cfg, new_cfg)
        migrated.append(f"{legacy_cfg.name} -> {new_cfg.name}")

    # Per-project sessions: ./.agentcli/sessions -> ./.arescode/sessions
    legacy_sessions = project_dir / LEGACY_PROJECT_STATE_DIR / "sessions"
    new_sessions = project_dir / ".arescode" / "sessions"
    if legacy_sessions.is_dir() and not new_sessions.exists():
        new_sessions.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy_sessions, new_sessions)
        migrated.append(f"{legacy_sessions} -> {new_sessions}")

    return migrated
