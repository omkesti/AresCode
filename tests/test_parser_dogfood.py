"""Parser gap surfaced by the Phase 6.5 dogfood run (docs/DOGFOOD.md, LATER.md).

qwen2.5-coder:7b, asked to fix a failing test, emitted an ``edit_file`` whose SEARCH/REPLACE block
used the *bare words* ``SEARCH`` / ``REPLACE`` with **no** git-conflict markers
(``<<<<<<< ======= >>>>>>>``). ``SR_RE`` requires those markers and ``_parse_tool`` has no
``edit_file`` branch, so nothing parsed and the edit was silently lost — the model's fix never
reached disk (the 14b, which emits proper markers, completed the same task end-to-end).

The raw completion lives in ``tests/fixtures/model_outputs/bare-search-replace-no-markers.txt``.
This is the "write their tests" half of decision D10: the test records the desired behavior and is
marked ``xfail`` until the author decides whether/how to harden the [HAND] parser for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arescode.core.parser import parse
from arescode.tools.registry import EditFileAction

_SAMPLE = (
    Path(__file__).parent / "fixtures" / "model_outputs" / "bare-search-replace-no-markers.txt"
).read_text(encoding="utf-8")


@pytest.mark.xfail(
    strict=True,
    reason="known gap (6.5 dogfood): bare SEARCH/REPLACE without <<<<<<< markers is not parsed; "
    "harden the [HAND] parser or leave as a recorded finding",
)
def test_bare_search_replace_without_conflict_markers_is_parsed():
    actions = parse(_SAMPLE).actions
    edits = [a for a in actions if isinstance(a, EditFileAction)]
    assert edits, "expected the edit_file block to be recovered despite missing conflict markers"
    edit = edits[0]
    assert edit.path == "mathutils.py"
    assert "return a - b" in edit.edits[0].search
    assert "return a + b" in edit.edits[0].replace
