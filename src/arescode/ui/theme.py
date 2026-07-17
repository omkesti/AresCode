"""AresCode visual theme: the purple palette and the block-letter wordmark.

The look mirrors the Claude Code CLI banner — a boxed welcome line above a chunky
pixel-art logo with a drop shadow — with AresCode's primary purple (#9933FF) in place
of Claude's orange. Every shade used for *text* stays light enough to read on a dark
terminal background; only the logo's decorative shadow dips darker.
"""

from __future__ import annotations

from rich.text import Text

# The brand purple and the shades built around it. PRIMARY carries the logo, borders,
# and accents; PRIMARY_LIGHT is for emphasized-but-readable text; SHADOW is decorative
# only (the logo's drop shadow) and is never used for text.
PRIMARY = "#9933FF"
PRIMARY_LIGHT = "#BB86FF"
SHADOW = "#6B24B2"

ACCENT = PRIMARY
ACCENT_BOLD = f"bold {PRIMARY}"

# 4x5 pixel font for the wordmark, matching the reference art: chunky flat-topped
# letters slightly taller than wide. Each glyph is a 5-row grid of 'X' (filled) and
# '.' (empty) cells, rendered two characters per cell so the pixels come out square
# in a terminal. Only the letters the brand needs.
_LETTERS: dict[str, tuple[str, ...]] = {
    "A": (
        "XXXX",
        "X..X",
        "XXXX",
        "X..X",
        "X..X",
    ),
    "R": (
        "XXXX",
        "X..X",
        "XXX.",
        "X..X",
        "X..X",
    ),
    "E": (
        "XXXX",
        "X...",
        "XXX.",
        "X...",
        "XXXX",
    ),
    "S": (
        "XXXX",
        "X...",
        "XXXX",
        "...X",
        "XXXX",
    ),
    "C": (
        "XXXX",
        "X...",
        "X...",
        "X...",
        "XXXX",
    ),
    "O": (
        "XXXX",
        "X..X",
        "X..X",
        "X..X",
        "XXXX",
    ),
    "D": (
        "XXX.",
        "X..X",
        "X..X",
        "X..X",
        "XXX.",
    ),
}

_GLYPH_ROWS = 5


def _filled_cells(word: str) -> tuple[set[tuple[int, int]], int]:
    """Cell coordinates (row, col) that are filled for ``word``, plus the total cell width."""
    filled: set[tuple[int, int]] = set()
    col = 0
    for letter in word:
        glyph = _LETTERS[letter]
        for r, row in enumerate(glyph):
            for c, cell in enumerate(row):
                if cell == "X":
                    filled.add((r, col + c))
        col += len(glyph[0]) + 1  # one blank cell between letters
    return filled, col - 1


def word_lines(word: str, *, indent: int = 2) -> list[Text]:
    """Render ``word`` as pixel-art lines: PRIMARY glyphs over a SHADOW copy offset one
    row down and one *character* (half a pixel cell) left, the tight outline-peek of the
    reference art. Compositing runs at character resolution — a cell is two characters
    wide, and a full-cell offset reads as far too detached — with ░ for the shadow so
    the letterforms stay crisp even where color is unavailable."""
    filled, width = _filled_cells(word)
    lines: list[Text] = []
    for r in range(_GLYPH_ROWS + 1):  # +1 row so the shadow's bottom edge fits
        line = Text(" " * indent)
        for x in range(-1, width * 2):  # from -1: the shadow juts one char left
            if x >= 0 and (r, x // 2) in filled:
                line.append("█", style=PRIMARY)
            elif (r - 1, (x + 1) // 2) in filled:
                line.append("░", style=SHADOW)
            else:
                line.append(" ")
        lines.append(line)
    return lines


def logo_lines(*, indent: int = 2) -> list[Text]:
    """The stacked AresCode wordmark: ARES over CODE, ready to print line by line."""
    lines = word_lines("ARES", indent=indent)
    lines.extend(word_lines("CODE", indent=indent))
    return lines
