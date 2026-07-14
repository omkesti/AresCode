"""arescode CLI entry point: parse flags, load layered config, launch the REPL.

Validates the working directory, resolves configuration, then hands off to the interactive
chat loop in ``ui/repl.py`` (TASKS 1.3-1.6).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from arescode.config import load_config, migrate_legacy_paths

app = typer.Typer(
    add_completion=False,
    help="AresCode - a local-model coding agent (Ollama-powered).",
)


def _validate_project_dir(path: Path) -> None:
    """Refuse to start unless cwd is a real, accessible directory the user owns."""
    if not path.is_dir():
        raise typer.BadParameter(f"{path} is not a directory")
    if not os.access(path, os.R_OK | os.W_OK):
        typer.echo(f"error: {path} is not readable/writable by the current user", err=True)
        raise typer.Exit(code=1)
    # POSIX-only ownership check; Windows has no getuid(), so we rely on access() above.
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        try:
            if path.stat().st_uid != getuid():
                typer.echo(f"warning: {path} is not owned by the current user", err=True)
        except OSError:
            pass


@app.command()
def start(
    model: str | None = typer.Option(None, "--model", help="Override the model name."),
    ctx: int | None = typer.Option(
        None, "--ctx", help="Override the context window size (num_ctx)."
    ),
    resume: bool = typer.Option(False, "--resume", help="Resume the most recent session."),
    yolo: bool = typer.Option(
        False, "--yolo", help="Auto-approve every action (dangerous; hard denials still apply)."
    ),
) -> None:
    """Launch the agent REPL in the current directory."""
    project_dir = Path.cwd()
    _validate_project_dir(project_dir)

    # One-time rename migration: copy any agent-cli-era config/sessions to the arescode paths.
    migrated = migrate_legacy_paths(project_dir)
    if migrated:
        typer.echo(f"info: migrated legacy agent-cli data to AresCode ({'; '.join(migrated)})")

    config = load_config(project_dir=project_dir, overrides={"model": model, "num_ctx": ctx})

    # Imported lazily so `--help` and startup stay fast and don't pull in the UI stack.
    from arescode.ui.repl import run

    asyncio.run(run(config=config, project_dir=project_dir, resume=resume, yolo=yolo))


def main() -> None:
    """Console-script and ``python -m arescode`` entry point."""
    app()


if __name__ == "__main__":
    main()
