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
