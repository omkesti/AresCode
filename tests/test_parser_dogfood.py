"""Parser gap surfaced by the Phase 6.5 dogfood run (docs/DOGFOOD.md, LATER.md), now fixed.

qwen2.5-coder:7b, asked to fix a failing test, emitted an ``edit_file`` whose SEARCH/REPLACE block
used the *bare words* ``SEARCH`` / ``REPLACE`` with **no** git-conflict markers
(``<<<<<<< ======= >>>>>>>``). ``SR_RE`` requires those markers and ``_parse_tool`` had no
``edit_file`` branch, so nothing parsed and the edit was silently lost (the 14b, which emits proper
markers, completed the same task end-to-end).

The parser now rescues this via ``_bare_edit_block``, scoped to the ``<tool>edit_file</tool>``
window so ordinary prose containing the words SEARCH/REPLACE is never misread as an edit. The raw
completion lives in ``tests/fixtures/model_outputs/bare-search-replace-no-markers.txt``.
"""

from __future__ import annotations

from pathlib import Path

from arescode.core.parser import parse
from arescode.tools.registry import EditFileAction

_SAMPLE = (
    Path(__file__).parent / "fixtures" / "model_outputs" / "bare-search-replace-no-markers.txt"
).read_text(encoding="utf-8")


def test_bare_search_replace_without_conflict_markers_is_parsed():
    actions = parse(_SAMPLE).actions
    edits = [a for a in actions if isinstance(a, EditFileAction)]
    assert edits, "expected the edit_file block to be recovered despite missing conflict markers"
    edit = edits[0]
    assert edit.path == "mathutils.py"
    assert "return a - b" in edit.edits[0].search
    assert "return a + b" in edit.edits[0].replace
    # The recovered SEARCH must match the file byte-for-byte, so the exact cascade tier applies.
    assert edit.edits[0].search == (
        'def add(a, b):\n    """Return the sum of a and b."""\n    return a - b'
    )


def test_bare_search_replace_fenced_variant_is_parsed():
    # The other shape the 7b produced: bold labels + ```python fences around each side.
    sample = (
        "<tool>edit_file</tool><path>mathutils.py</path>\n\n"
        "**SEARCH**\n```python\ndef add(a, b):\n    return a - b\n```\n\n"
        "**REPLACE**\n```python\ndef add(a, b):\n    return a + b\n```\n"
    )
    edits = [a for a in parse(sample).actions if isinstance(a, EditFileAction)]
    assert len(edits) == 1
    assert edits[0].edits[0].search == "def add(a, b):\n    return a - b"
    assert edits[0].edits[0].replace == "def add(a, b):\n    return a + b"


def test_bare_keywords_outside_edit_tag_do_not_create_an_edit():
    # The scoping guard: SEARCH/REPLACE as ordinary prose (no <tool>edit_file</tool>) must NOT be
    # mistaken for an edit — otherwise the fix would false-positive on normal model chatter.
    prose = (
        "Here is my plan:\n\nSEARCH the codebase for the bug,\nthen REPLACE the faulty line.\n"
    )
    edits = [a for a in parse(prose).actions if isinstance(a, EditFileAction)]
    assert edits == []


def test_conflict_marker_edit_still_parses():
    # Regression: the normal, correct SEARCH/REPLACE format must keep working unchanged.
    sample = (
        "src/app.py\n<<<<<<< SEARCH\nold_line = 1\n=======\nnew_line = 2\n>>>>>>> REPLACE\n"
    )
    edits = [a for a in parse(sample).actions if isinstance(a, EditFileAction)]
    assert len(edits) == 1
    assert edits[0].path == "src/app.py"
    assert edits[0].edits[0].search == "old_line = 1"
    assert edits[0].edits[0].replace == "new_line = 2"


# --- Finding C: tag-less / marker-less whole-file code block (7b baseline 2026-07-18) -------------

_TAGLESS = (
    Path(__file__).parent / "fixtures" / "model_outputs" / "tagless-whole-file-codeblock.txt"
).read_text(encoding="utf-8")


def test_tagless_filename_codeblock_recovered_as_whole_file_edit():
    # The measured 7b failure: no <tool> tag, no SEARCH/REPLACE markers — just `**ops.py**` then the
    # full new file in a fence. Recovered as an empty-SEARCH whole-file edit.
    edits = [a for a in parse(_TAGLESS).actions if isinstance(a, EditFileAction)]
    assert len(edits) == 1
    assert edits[0].path == "ops.py"
    assert edits[0].edits[0].search == ""  # empty SEARCH => whole-file rewrite
    assert edits[0].edits[0].replace == "def add(a, b):\n    return a + b"


def test_recovery_skipped_when_another_action_present():
    # The guard: recovery fires ONLY when nothing else parsed. A turn that also issues a real tool
    # call must not have an incidental filename+fence turned into a second (whole-file) edit.
    sample = (
        "Let me check first.\n<tool>read_file</tool><path>ops.py</path>\n\n"
        "**ops.py**\n```python\ndef add(a, b):\n    return a + b\n```\n"
    )
    actions = parse(sample).actions
    assert not [a for a in actions if isinstance(a, EditFileAction)]
    assert any(getattr(a, "tool", "") == "read_file" for a in actions)


def test_edit_file_tag_with_fence_no_markers_is_whole_file():
    # Tagged variant of Finding C: the tag is present but the model pastes the full file in a fence
    # with no SEARCH/REPLACE markers -> whole-file edit scoped to the tag window.
    sample = (
        "<tool>edit_file</tool><path>ops.py</path>\n"
        "```python\ndef add(a, b):\n    return a + b\n```\n"
    )
    edits = [a for a in parse(sample).actions if isinstance(a, EditFileAction)]
    assert len(edits) == 1
    assert edits[0].path == "ops.py"
    assert edits[0].edits[0].search == ""
    assert edits[0].edits[0].replace == "def add(a, b):\n    return a + b"


def test_incidental_codeblock_without_filename_header_is_not_an_edit():
    # A fenced code block with no filename header just above it is not an edit (no false positive).
    prose = (
        "Here's roughly what the function should look like:\n\n"
        "```python\ndef add(a, b):\n    return a + b\n```\n\nDoes that make sense?"
    )
    assert [a for a in parse(prose).actions if isinstance(a, EditFileAction)] == []


def test_edit_tag_stray_fence_and_filename_header_extracts_code_not_header():
    # 7b, task 1 (Run A): <tool>edit_file</tool> + a stray empty fence + a **filename** header +
    # the real code fence. _fenced_blocks used to capture "**ops.py**" as the body (→ compiles to a
    # syntax error, edit rejected). The filename-only block must be skipped so the code is used.
    sample = (
        "<tool>edit_file</tool><path>ops.py</path>\n"
        "```\n\n**ops.py**\n```python\ndef add(a, b):\n    return a + b\n```\n"
    )
    edits = [a for a in parse(sample).actions if isinstance(a, EditFileAction)]
    assert len(edits) == 1
    assert edits[0].edits[0].search == ""
    assert edits[0].edits[0].replace == "def add(a, b):\n    return a + b"
