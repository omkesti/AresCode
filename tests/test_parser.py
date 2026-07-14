"""Parser corpus (TASKS 2.2): table-driven cases over every tolerance in the parser (2.1).

Cases are hand-authored to mimic the malformed shapes a 7B model produces; the corpus is meant
to grow with real recorded outputs collected during development (working agreement #4).
"""

from __future__ import annotations

import textwrap

import pytest

from arescode.core.parser import parse, parse_actions
from arescode.tools.registry import (
    BashAction,
    EditFileAction,
    GlobAction,
    GrepAction,
    ReadFileAction,
    SearchReplace,
    WriteFileAction,
)


def d(text: str) -> str:
    return textwrap.dedent(text).strip("\n")


CASES: list[tuple[str, str, list]] = [
    ("read_simple", "<tool>read_file</tool><path>src/auth.py</path>", [ReadFileAction("src/auth.py")]),
    (
        "read_offset_limit",
        "<tool>read_file</tool><path>a.py</path><offset>40</offset><limit>60</limit>",
        [ReadFileAction("a.py", 40, 60)],
    ),
    ("read_bad_offset", "<tool>read_file</tool><path>a.py</path><offset>xx</offset>", [ReadFileAction("a.py", None, None)]),
    ("read_missing_close_tag", "<tool>read_file</tool><path>src/x.py", [ReadFileAction("src/x.py")]),
    ("read_tag_whitespace", "<tool> read_file </tool><path>  src/auth.py  </path>", [ReadFileAction("src/auth.py")]),
    ("bash_simple", "<tool>bash</tool><cmd>python -m pytest -q</cmd>", [BashAction("python -m pytest -q")]),
    ("bash_redirect", "<tool>bash</tool><cmd>echo hi > out.txt</cmd>", [BashAction("echo hi > out.txt")]),
    ("bash_command_alias", "<tool>bash</tool><command>ls -la</command>", [BashAction("ls -la")]),
    ("grep_simple", "<tool>grep</tool><pattern>def login</pattern>", [GrepAction("def login")]),
    (
        "grep_scoped",
        "<tool>grep</tool><pattern>login</pattern><path>src</path><glob>*.py</glob>",
        [GrepAction("login", "src", "*.py")],
    ),
    ("grep_regex", r"<tool>grep</tool><pattern>def login\(</pattern>", [GrepAction(r"def login\(")]),
    ("glob_simple", "<tool>glob</tool><pattern>**/*.py</pattern>", [GlobAction("**/*.py")]),
    ("list_dir_alias", "<tool>list_dir</tool><path>src</path>", [GlobAction("src")]),
    ("plain_text", "The login function is in auth.py and the tests pass.", []),
    ("unknown_tool", "<tool>frobnicate</tool><path>x</path>", []),
    (
        "two_actions",
        "<tool>read_file</tool><path>a.py</path>\n<tool>bash</tool><cmd>pytest</cmd>",
        [ReadFileAction("a.py"), BashAction("pytest")],
    ),
    (
        "two_reads_ordered",
        "<tool>read_file</tool><path>a.py</path>\n<tool>read_file</tool><path>b.py</path>",
        [ReadFileAction("a.py"), ReadFileAction("b.py")],
    ),
    (
        "edit_standard",
        d(
            """
            src/auth.py
            <<<<<<< SEARCH
            def login(user):
            =======
            def login(user, remember=False):
            >>>>>>> REPLACE
            """
        ),
        [EditFileAction("src/auth.py", (SearchReplace("def login(user):", "def login(user, remember=False):"),))],
    ),
    (
        "edit_marker_drift",
        d(
            """
            src/auth.py
            <<<<<< SEARCH
            a
            ====
            b
            >>>>>>>> REPLACE
            """
        ),
        [EditFileAction("src/auth.py", (SearchReplace("a", "b"),))],
    ),
    (
        "edit_filename_blank_line_above",
        d(
            """
            src/auth.py

            <<<<<<< SEARCH
            a
            =======
            b
            >>>>>>> REPLACE
            """
        ),
        [EditFileAction("src/auth.py", (SearchReplace("a", "b"),))],
    ),
    (
        "edit_path_tag_above",
        d(
            """
            <path>src/auth.py</path>
            <<<<<<< SEARCH
            a
            =======
            b
            >>>>>>> REPLACE
            """
        ),
        [EditFileAction("src/auth.py", (SearchReplace("a", "b"),))],
    ),
    (
        "edit_empty_search_new_file",
        d(
            """
            src/new.py
            <<<<<<< SEARCH
            =======
            print("hi")
            >>>>>>> REPLACE
            """
        ),
        [EditFileAction("src/new.py", (SearchReplace("", 'print("hi")'),))],
    ),
    (
        "edit_two_blocks_same_file",
        d(
            """
            src/auth.py
            <<<<<<< SEARCH
            a
            =======
            b
            >>>>>>> REPLACE
            <<<<<<< SEARCH
            c
            =======
            d
            >>>>>>> REPLACE
            """
        ),
        [
            EditFileAction("src/auth.py", (SearchReplace("a", "b"),)),
            EditFileAction("src/auth.py", (SearchReplace("c", "d"),)),
        ],
    ),
    (
        "edit_no_filename_dropped",
        d(
            """
            <<<<<<< SEARCH
            a
            =======
            b
            >>>>>>> REPLACE
            """
        ),
        [],
    ),
    (
        "edit_multiline_body",
        d(
            """
            src/m.py
            <<<<<<< SEARCH
            x = 1
            y = 2
            =======
            x = 10
            y = 20
            >>>>>>> REPLACE
            """
        ),
        [EditFileAction("src/m.py", (SearchReplace("x = 1\ny = 2", "x = 10\ny = 20"),))],
    ),
    (
        "write_file_fenced",
        d(
            """
            <tool>write_file</tool><path>hello.py</path>
            ```python
            print("hi")
            ```
            """
        ),
        [WriteFileAction("hello.py", 'print("hi")')],
    ),
    (
        "write_file_no_lang_fence",
        d(
            """
            <tool>write_file</tool><path>notes.txt</path>
            ```
            line one
            line two
            ```
            """
        ),
        [WriteFileAction("notes.txt", "line one\nline two")],
    ),
    (
        "prose_around_action",
        "Let me check the file.\n<tool>read_file</tool><path>a.py</path>\nThen I'll decide.",
        [ReadFileAction("a.py")],
    ),
    (
        "mixed_read_then_edit",
        d(
            """
            <tool>read_file</tool><path>src/auth.py</path>
            src/auth.py
            <<<<<<< SEARCH
            a
            =======
            b
            >>>>>>> REPLACE
            """
        ),
        [ReadFileAction("src/auth.py"), EditFileAction("src/auth.py", (SearchReplace("a", "b"),))],
    ),
]


@pytest.mark.parametrize("text,expected", [(t, e) for _, t, e in CASES], ids=[c[0] for c in CASES])
def test_parse_actions(text, expected):
    assert parse_actions(text) == expected


def test_corpus_size():
    # TASKS 2.2 requires at least 25 cases.
    assert len(CASES) >= 25


def test_prose_is_stripped_of_actions():
    result = parse("Checking now.\n<tool>read_file</tool><path>a.py</path>\nDone.")
    assert result.actions == [ReadFileAction("a.py")]
    assert "read_file" not in result.prose
    assert "Checking now." in result.prose and "Done." in result.prose


def test_plain_answer_has_no_actions():
    result = parse("The `login` function handles login, and all 3 tests pass.")
    assert result.actions == []
    assert "login" in result.prose
