"""Tests for the interactive approval menu (permission prompt).

The terminal keystroke read isn't unit-tested (it needs a real TTY); the pure key->transition
logic, the option builder, and the non-interactive line fallback are.
"""

from __future__ import annotations

import io

from rich.console import Console

from arescode.tools.registry import BashAction, EditFileAction, SearchReplace, WriteFileAction
from arescode.ui.approve import (
    _build_options,
    _menu_select_line,
    _render_request,
    _resolve_menu_key,
    interactive_approver,
)


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False)


# --- option building -------------------------------------------------------


def test_options_for_file_edit_offer_always_for_file():
    action = EditFileAction("a.py", (SearchReplace("x", "y"),))
    v = _mk_verdict(scope="file", key="a.py")
    options = _build_options(action, v)
    labels = [label for label, _ in options]
    assert labels[0].startswith("Yes")
    assert labels[1].startswith("No")
    assert "a.py" in labels[2]
    assert options[2][1].remember is True  # 'always' remembers


def test_options_for_bash_say_run_and_offer_always_for_command():
    v = _mk_verdict(scope="command", key="pytest")
    options = _build_options(BashAction("pytest -q"), v)
    assert options[0][0] == "Yes, run it"
    assert "pytest" in options[2][0]


def test_options_without_scope_have_no_always():
    v = _mk_verdict(scope="", key="")
    options = _build_options(WriteFileAction("a.py", "x"), v)
    assert len(options) == 2  # only yes / no


# --- key resolution --------------------------------------------------------


def test_arrow_keys_wrap_around():
    assert _resolve_menu_key("down", 0, 3) == (1, "move")
    assert _resolve_menu_key("up", 0, 3) == (2, "move")  # wraps to last
    assert _resolve_menu_key("down", 2, 3) == (0, "move")  # wraps to first


def test_enter_selects_current_and_esc_cancels():
    assert _resolve_menu_key("enter", 1, 3) == (1, "select")
    assert _resolve_menu_key("esc", 1, 3) == (1, "cancel")


def test_letter_and_number_shortcuts():
    assert _resolve_menu_key("y", 0, 3) == (0, "select")
    assert _resolve_menu_key("n", 0, 3) == (1, "select")
    assert _resolve_menu_key("a", 0, 3) == (2, "select")
    assert _resolve_menu_key("a", 0, 2)[1] == "move"  # no 'always' option -> not selectable
    assert _resolve_menu_key("2", 0, 3) == (1, "select")
    assert _resolve_menu_key("9", 0, 3) == (0, "move")  # out of range -> ignored


# --- non-interactive fallback (piped stdin) --------------------------------


def test_line_fallback_maps_answers(monkeypatch):
    options = _build_options(BashAction("ls"), _mk_verdict(scope="command", key="ls"))
    monkeypatch.setattr("builtins.input", lambda: "a")
    assert _menu_select_line(options) == 2  # 'always'
    monkeypatch.setattr("builtins.input", lambda: "y")
    assert _menu_select_line(options) == 0
    monkeypatch.setattr("builtins.input", lambda: "")
    assert _menu_select_line(options) is None  # empty declines


def test_render_request_escapes_brackets_no_markup_error():
    # A path with brackets must not blow up rich markup parsing (the [y]/[n]/[a] bug class).
    console = _console()
    _render_request(console, WriteFileAction("weird[name].py", "x"), _mk_verdict(), "")
    out = console.file.getvalue()
    assert "weird[name].py" in out
    assert "permission needed" in out


def test_interactive_approver_declines_on_empty_piped_input(monkeypatch):
    # Under pytest stdin is non-interactive, so the approver uses the line fallback; empty declines.
    monkeypatch.setattr("builtins.input", lambda: "")
    approver = interactive_approver(_console())
    approval = approver(WriteFileAction("a.py", "x"), _mk_verdict(scope="file", key="a.py"), "")
    assert approval.approved is False


def test_interactive_approver_approves_and_remembers_on_always(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda: "a")
    approver = interactive_approver(_console())
    approval = approver(WriteFileAction("a.py", "x"), _mk_verdict(scope="file", key="a.py"), "")
    assert approval.approved is True and approval.remember is True


# --- helpers ---------------------------------------------------------------


def _mk_verdict(*, scope: str = "", key: str = ""):
    from arescode.permissions.gate import Decision, Verdict

    return Verdict(Decision.ASK, scope=scope, key=key)
