"""Text wrapping and pagination for the G1 HUD. Pure functions."""

from __future__ import annotations

import textwrap

# The official demo renders ~488px-wide text at font size 21 (proportional),
# five lines per screen. 40 monospace-ish chars is a conservative fit;
# tune with the --chars flag once we see it on real hardware.
DEFAULT_CHARS_PER_LINE = 40
DEFAULT_LINES_PER_PAGE = 5


def wrap_text(text: str, max_chars: int = DEFAULT_CHARS_PER_LINE) -> list[str]:
    """Word-wrap text into display lines; blank lines are dropped (rows are scarce)."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        stripped = paragraph.strip()
        if not stripped:
            continue
        lines.extend(
            textwrap.wrap(
                stripped,
                width=max_chars,
                break_long_words=True,
                break_on_hyphens=False,
            )
        )
    return lines


def paginate(
    text: str,
    *,
    max_chars: int = DEFAULT_CHARS_PER_LINE,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
) -> list[str]:
    """Split text into HUD pages of at most `lines_per_page` newline-joined lines."""
    lines = wrap_text(text, max_chars)
    return [
        "\n".join(lines[i : i + lines_per_page])
        for i in range(0, len(lines), lines_per_page)
    ]
