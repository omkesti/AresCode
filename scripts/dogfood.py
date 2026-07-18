"""Headless dogfood gauntlet: drive the real agent loop over the local model (DOGFOOD.md).

Turns DOGFOOD.md's manual three-task gauntlet into one push-button command so edit-success can be
re-measured any time the harness or prompt changes — the `/stats` north-star check (working
agreement 5). Unlike the pytest suite (which never touches a live model), this needs a running
Ollama + the target model pulled; it is the on-device check the author runs, not CI.

For each task it materializes a deterministic buggy scratch repo in a temp dir, then drives the
*real* pipeline — provider -> run_turn -> parser -> gate -> executor — with the `--yolo`
auto-approver so it is non-interactive, and applies a task-specific success check. Per task it
prints the DOGFOOD.md log template + the EditStats summary (`/stats`); results are printed, not
appended to LATER.md, so the author curates what lands. On any parse/edit miss it dumps the raw
model completions to `tests/fixtures/model_outputs/` so a weird output becomes a parser-corpus
candidate (working agreement 4).

Usage:
    python scripts/dogfood.py [--model TAG] [--ctx N] [--base-url URL]
                              [--tasks 1,2,3] [--verbose] [--keep]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# Make the package importable when run as `python scripts/dogfood.py` without an editable install,
# and let `import check_ollama` find the sibling script (scripts/ is already sys.path[0] when run
# directly, but be explicit so it also works when imported).
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (_REPO_ROOT / "src", _REPO_ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from check_ollama import run_checks  # noqa: E402  (path set up above)

from arescode.config import Config  # noqa: E402
from arescode.core.context import assemble_system_prompt  # noqa: E402
from arescode.core.loop import run_turn  # noqa: E402
from arescode.core.state import SessionState  # noqa: E402
from arescode.permissions.gate import Gate  # noqa: E402
from arescode.providers.openai_compat import OpenAICompatProvider  # noqa: E402
from arescode.repo.repomap import build_repo_map  # noqa: E402
from arescode.tools.registry import Executor  # noqa: E402
from arescode.ui import render  # noqa: E402
from arescode.ui.approve import auto_approver  # noqa: E402

FIXTURES = _REPO_ROOT / "tests" / "fixtures" / "model_outputs"


# ---------------------------------------------------------------------------
# The three tasks (DOGFOOD.md § "The three tasks")
# ---------------------------------------------------------------------------


@dataclass
class Task:
    tid: str  # short id used in temp-dir / fixture-dump names
    title: str  # one-line description for the log
    prompt: str  # the user turn handed to the agent
    files: dict[str, str]  # scratch-repo contents (relpath -> text)
    verdict: Callable[[Path], tuple[bool, str]]  # (passed?, one-line detail)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a scratch-repo command with the current interpreter; capture output, never raise."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=False
    )


def _verdict_fix_test(workdir: Path) -> tuple[bool, str]:
    proc = _run([sys.executable, "-m", "pytest", "-q"], workdir)
    tail = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
    return proc.returncode == 0, f"pytest exit {proc.returncode}: {tail}"


def _verdict_add_feature(workdir: Path) -> tuple[bool, str]:
    mathutil = (workdir / "mathutil.py").read_text(encoding="utf-8")
    has_cube = "def cube" in mathutil
    proc = _run([sys.executable, "app.py"], workdir)
    runs = proc.returncode == 0 and "125" in proc.stdout and "25" in proc.stdout
    out = proc.stdout.split() or proc.stderr.strip()[:60]
    detail = f"def cube={has_cube}, app.py exit {proc.returncode}, out={out!r}"
    return (has_cube and runs), detail


def _verdict_rename(workdir: Path) -> tuple[bool, str]:
    greeter = (workdir / "greeter.py").read_text(encoding="utf-8")
    renamed = "def greet(person" in greeter and "def greet(name" not in greeter
    main_txt = (workdir / "main.py").read_text(encoding="utf-8")
    no_stale = "name=" not in main_txt
    proc = _run([sys.executable, "main.py"], workdir)
    runs = proc.returncode == 0
    detail = f"renamed={renamed}, callers_updated={no_stale}, main.py exit {proc.returncode}"
    return (renamed and no_stale and runs), detail


TASKS: list[Task] = [
    Task(
        tid="fix-test",
        title="fix a failing test (buggy add returns a - b)",
        prompt=(
            "The test in test_ops.py is failing. Find the bug in the code, fix it, then run the "
            "tests with pytest and confirm they pass."
        ),
        files={
            "ops.py": "def add(a, b):\n    return a - b\n",
            "test_ops.py": (
                "from ops import add\n\n\n"
                "def test_add():\n    assert add(2, 3) == 5\n"
            ),
        },
        verdict=_verdict_fix_test,
    ),
    Task(
        tid="add-feature",
        title="add a function across two files (cube + its caller)",
        prompt=(
            "Add a function named `cube` to mathutil.py that takes one argument `n` and returns "
            "n cubed (n ** 3). Then edit app.py so main() also prints cube(5) on its own line "
            "after the existing square line, importing cube where needed. The program must run."
        ),
        files={
            "mathutil.py": "def square(n):\n    return n * n\n",
            "app.py": (
                "from mathutil import square\n\n\n"
                "def main():\n    print(square(5))\n\n\n"
                'if __name__ == "__main__":\n    main()\n'
            ),
        },
        verdict=_verdict_add_feature,
    ),
    Task(
        tid="rename-param",
        title="rename a parameter and update keyword callers",
        prompt=(
            "In greeter.py, rename the parameter `name` of the function greet to `person`, "
            "updating the function body to use `person`. Then update every caller in main.py "
            "that passes the argument by keyword so it uses `person=` instead of `name=`. The "
            "program must still run."
        ),
        files={
            "greeter.py": 'def greet(name):\n    return "Hi " + name\n',
            "main.py": (
                "from greeter import greet\n\n"
                'print(greet(name="Ada"))\n'
                'print(greet(name="Bob"))\n'
            ),
        },
        verdict=_verdict_rename,
    ),
]


# ---------------------------------------------------------------------------
# Running one task through the real loop
# ---------------------------------------------------------------------------


@dataclass
class TaskReport:
    task: Task
    passed: bool
    detail: str
    stats_summary: str
    stats_failures: int
    used_fallback_tier: bool  # fuzzy / whole_file was needed (a "landed after retries" signal)
    dump_path: Path | None
    workdir: Path
    final_text: str
    steps_terminated: bool  # did the loop end on its own (not the step cap / a stall)?


def _materialize(workdir: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = workdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _dump_completions(task: Task, state: SessionState, stamp: str) -> Path:
    """Save the turn's raw assistant completions as a parser-corpus candidate (agreement 4)."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    completions = [m.content for m in state.messages if m.role == "assistant"]
    body = f"\n\n{'=' * 70}\n".join(completions) or "(no assistant output)"
    path = FIXTURES / f"dogfood-{task.tid}-{stamp}.txt"
    header = (
        f"# Raw model completions from a dogfood miss — task '{task.tid}': {task.title}\n"
        f"# {len(completions)} assistant step(s). Curate into a real corpus case if it shows a\n"
        f"# genuine parser/edit gap, then delete this dump.\n\n"
    )
    path.write_text(header + body, encoding="utf-8")
    return path


async def run_one(task: Task, config: Config, *, verbose: bool, keep: bool) -> TaskReport:
    workdir = Path(tempfile.mkdtemp(prefix=f"arescode-dogfood-{task.tid}-"))
    _materialize(workdir, task.files)

    console = render.make_console()
    provider = OpenAICompatProvider.from_config(config)
    gate = Gate.from_config(workdir, config)
    executor = Executor(workdir, config, gate=gate)
    approver = auto_approver(console)
    repo_map = build_repo_map(workdir)
    system_prompt = assemble_system_prompt(workdir, repo_map=repo_map)
    state = SessionState.new(config.model)
    observer = render.ConsoleObserver(console, verbose=verbose)

    console.print(f"\n[bold]▶ {task.tid}[/bold]  {task.title}")
    final_text = await run_turn(
        task.prompt,
        state=state,
        provider=provider,
        executor=executor,
        system_prompt=system_prompt,
        observer=observer,
        max_steps=config.max_steps,
        gate=gate,
        approver=approver,
        num_ctx=config.num_ctx,
    )

    passed, detail = task.verdict(workdir)
    stats = executor.stats
    # The loop returns a fixed sentence when it hits the step cap or stalls; anything else means the
    # model chose to stop (a clean autonomous finish, which the gauntlet cares about).
    terminated = (
        not final_text.startswith("Reached the step limit")
        and "kept repeating" not in final_text
    )

    dump_path: Path | None = None
    if not passed or stats.failures > 0:
        dump_path = _dump_completions(task, state, date.today().strftime("%Y%m%d"))

    if passed and not keep:
        shutil.rmtree(workdir, ignore_errors=True)

    return TaskReport(
        task=task,
        passed=passed,
        detail=detail,
        stats_summary=stats.summary(),
        stats_failures=stats.failures,
        used_fallback_tier=bool(stats.fuzzy or stats.whole_file),
        dump_path=dump_path,
        workdir=workdir,
        final_text=final_text.strip(),
        steps_terminated=terminated,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _outcome(report: TaskReport) -> str:
    if not report.passed:
        return "failed"
    if report.stats_failures or report.used_fallback_tier:
        return "landed after retries"
    return "landed clean"


def _print_report(report: TaskReport, config: Config, project: str, n: int) -> None:
    """The DOGFOOD.md logging template, filled in — copy/curate the relevant ones into LATER.md."""
    r = report
    dump = str(r.dump_path) if r.dump_path else "(none)"
    print()
    print(f"### {date.today().isoformat()} — {project} — task {n}: {r.task.title}")
    print(f"- Model: {config.model}   num_ctx: {config.num_ctx}")
    print(
        f"- Outcome: {_outcome(r)}  "
        f"(autonomous finish: {r.steps_terminated}; verdict: {r.detail})"
    )
    print(f"- /stats: {r.stats_summary}")
    print(f"- Failure(s): {'—' if r.passed else r.final_text or 'verdict check failed'}")
    print(f"- Raw model output (if a parse/edit miss): {dump}")
    if not r.passed:
        print(f"- Scratch repo kept for inspection: {r.workdir}")


async def _sweep(config: Config, tasks: list[Task], *, verbose: bool, keep: bool) -> int:
    reports: list[TaskReport] = []
    for i, task in enumerate(tasks, 1):
        report = await run_one(task, config, verbose=verbose, keep=keep)
        reports.append(report)
        _print_report(report, config, project="dogfood-scratch", n=i)

    passed = sum(1 for r in reports if r.passed)
    total = len(reports)
    print("\n" + "=" * 70)
    print(f"OVERALL: {passed}/{total} tasks passed" + ("  ✅" if passed == total else "  ❌"))
    for i, r in enumerate(reports, 1):
        mark = "PASS" if r.passed else "FAIL"
        print(f"  [{mark}] task {i} ({r.task.tid}): {_outcome(r)}")
    print("=" * 70)
    return 0 if passed == total else 1


def _ollama_root(base_url: str) -> str:
    """The Ollama server root (no ``/v1``) that check_ollama expects, from a Config base_url."""
    base = base_url.rstrip("/")
    return base[:-3].rstrip("/") if base.endswith("/v1") else base


def _select_tasks(spec: str | None) -> list[Task]:
    if not spec:
        return TASKS
    try:
        idxs = [int(x) for x in spec.split(",") if x.strip()]
    except ValueError as exc:
        raise SystemExit(
            f"--tasks must be comma-separated task numbers (1..{len(TASKS)}), got {spec!r}"
        ) from exc
    chosen = []
    for i in idxs:
        if not 1 <= i <= len(TASKS):
            raise SystemExit(f"task {i} out of range (1..{len(TASKS)})")
        chosen.append(TASKS[i - 1])
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless dogfood gauntlet for AresCode.")
    parser.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model tag to drive.")
    parser.add_argument("--ctx", type=int, default=16384, help="num_ctx for the run.")
    parser.add_argument("--base-url", default="http://localhost:11434/v1",
                        help="OpenAI-compatible endpoint (Config default).")
    parser.add_argument("--tasks", default=None,
                        help="comma-separated subset, e.g. '1,3' (default: all three).")
    parser.add_argument("--verbose", action="store_true",
                        help="show full tool output in the trace.")
    parser.add_argument("--keep", action="store_true",
                        help="keep scratch repos even when a task passes.")
    args = parser.parse_args()

    # This tool is meant to be redirected to a log, and it prints non-ASCII glyphs (—, ▶, ✅) in
    # its own report lines as well as via the rich trace. Force UTF-8 up front so a captured run on
    # a Windows cp1252 pipe can't die with UnicodeEncodeError (the same class as dogfood Finding B).
    render.force_utf8(sys.stdout)

    # Preflight: reuse check_ollama so a down server / missing model prints the exact fix and we
    # bail before spinning up the loop against nothing.
    print("Preflight (Ollama):")
    if run_checks(_ollama_root(args.base_url), args.model, args.ctx) != 0:
        print("\nPreflight failed — fix the above and re-run.")
        return 2
    print()

    config = Config(model=args.model, num_ctx=args.ctx, base_url=args.base_url)
    tasks = _select_tasks(args.tasks)
    return asyncio.run(_sweep(config, tasks, verbose=args.verbose, keep=args.keep))


if __name__ == "__main__":
    sys.exit(main())
