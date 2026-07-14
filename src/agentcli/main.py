"""agentcli CLI entry point: parse flags, load layered config, bootstrap the session.

Phase 0 wires the entry point end to end: validate the working directory, load
configuration, and report the active model and context size. The interactive REPL
(``ui/repl.py``) is added in Phase 1 (TASKS 1.3).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from agentcli.config import load_config

app = typer.Typer(add_completion=False, help="A local-model coding agent (Ollama-powered).")


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
    model: Optional[str] = typer.Option(None, "--model", help="Override the model name."),
    ctx: Optional[int] = typer.Option(
        None, "--ctx", help="Override the context window size (num_ctx)."
    ),
) -> None:
    """Launch the agent in the current directory."""
    project_dir = Path.cwd()
    _validate_project_dir(project_dir)

    config = load_config(project_dir=project_dir, overrides={"model": model, "num_ctx": ctx})

    typer.echo(f"agentcli · model={config.model} · num_ctx={config.num_ctx}")
    typer.echo(f"project: {project_dir}")
    # Phase 1 replaces the line below with the interactive REPL (ui/repl.py).
    typer.echo("(Phase 0 scaffolding — the interactive REPL arrives in Phase 1. Exiting cleanly.)")


def main() -> None:
    """Console-script and ``python -m agentcli`` entry point."""
    app()


if __name__ == "__main__":
    main()
