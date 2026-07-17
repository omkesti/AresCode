"""AresCode visual theme: the purple palette and the block-letter wordmark.

The look mirrors the Claude Code CLI banner — a boxed welcome line above a chunky
block-letter logo — with AresCode's primary purple (#9933FF) in place of Claude's
orange. Every shade used for *text* stays light enough to read on a dark terminal
background; only the wordmark's decorative shadow dips darker.
"""

from __future__ import annotations

from rich.text import Text

# The brand purple and the shades built around it. PRIMARY carries the logo, borders,
# and accents; PRIMARY_LIGHT is for emphasized-but-readable text; SHADOW is decorative
# only (the wordmark's shadow edge) and is never used for text.
PRIMARY = "#9933FF"
PRIMARY_LIGHT = "#BB86FF"
SHADOW = "#6B24B2"

ACCENT = PRIMARY
ACCENT_BOLD = f"bold {PRIMARY}"

# Figlet "ANSI Shadow"-style glyphs: solid █ letters with box-drawing characters as a
# thin shadow along the right/bottom edges. Glyphs concatenate directly, exactly as
# figlet lays them out — the spacing is built into each glyph. Only the letters the
# brand needs.
_LETTERS: dict[str, tuple[str, ...]] = {
    "A": (
        " █████╗ ",
        "██╔══██╗",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ),
    "R": (
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ),
    "E": (
        "███████╗",
        "██╔════╝",
        "█████╗  ",
        "██╔══╝  ",
        "███████╗",
        "╚══════╝",
    ),
    "S": (
        "███████╗",
        "██╔════╝",
        "███████╗",
        "╚════██║",
        "███████║",
        "╚══════╝",
    ),
    "C": (
        " ██████╗",
        "██╔════╝",
        "██║     ",
        "██║     ",
        "╚██████╗",
        " ╚═════╝",
    ),
    "O": (
        " ██████╗ ",
        "██╔═══██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ),
    "D": (
        "██████╗ ",
        "██╔══██╗",
        "██║  ██║",
        "██║  ██║",
        "██████╔╝",
        "╚═════╝ ",
    ),
}

_GLYPH_ROWS = 6


def word_lines(word: str, *, indent: int = 2) -> list[Text]:
    """Render ``word`` in the ANSI Shadow font: █ blocks in PRIMARY, with the
    box-drawing shadow characters in the darker SHADOW purple."""
    glyphs = [_LETTERS[letter] for letter in word]
    lines: list[Text] = []
    for r in range(_GLYPH_ROWS):
        line = Text(" " * indent)
        for ch in "".join(glyph[r] for glyph in glyphs):
            if ch == "█":
                line.append(ch, style=PRIMARY)
            elif ch == " ":
                line.append(ch)
            else:
                line.append(ch, style=SHADOW)
        lines.append(line)
    return lines


def logo_lines(*, indent: int = 2) -> list[Text]:
    """The stacked AresCode wordmark: ARES over CODE, ready to print line by line."""
    lines = word_lines("ARES", indent=indent)
    lines.extend(word_lines("CODE", indent=indent))
    return lines
