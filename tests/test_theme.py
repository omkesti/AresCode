"""The purple theme and pixel-art wordmark: glyph integrity and layout invariants."""

from __future__ import annotations

import io

from rich.console import Console

from arescode.ui import theme
from arescode.ui.render import banner


def test_glyphs_are_uniform_grids() -> None:
    charset = set("█╔╗╚╝═║ ")
    for letter, glyph in theme._LETTERS.items():
        assert len(glyph) == 6, f"{letter} should be 6 rows"
        assert len({len(row) for row in glyph}) == 1, f"{letter} rows should be equal width"
        assert all(set(row) <= charset for row in glyph), f"{letter} has stray characters"


def test_word_lines_are_rectangular() -> None:
    lines = theme.word_lines("ARES")
    assert len(lines) == 6
    widths = {len(line.plain) for line in lines}
    assert len(widths) == 1  # every line pads to the same width


def test_logo_stacks_both_words() -> None:
    lines = theme.logo_lines()
    assert len(lines) == 12  # ARES (6) + CODE (6)
    assert any("█" in line.plain for line in lines)
    assert any("╚" in line.plain for line in lines)  # the shadow edge rendered


def test_banner_shows_welcome_model_and_notes() -> None:
    console = Console(file=io.StringIO(), width=100, force_terminal=False, color_system=None)
    banner(console, model="qwen2.5-coder:7b", num_ctx=16384, project_dir="c:\\proj")
    out = console.file.getvalue()
    assert "Welcome to AresCode" in out
    assert "qwen2.5-coder:7b" in out and "num_ctx=16384" in out
    assert "c:\\proj" in out
    assert "Notes:" in out
    assert "█" in out  # the wordmark rendered
