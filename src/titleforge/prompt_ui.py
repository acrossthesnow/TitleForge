"""TTY clear + questionary list styling (no raw ANSI inside questionary messages)."""

from __future__ import annotations

import sys

from questionary import Style

# Pointer + highlighted row (current selection) for select menus
LIST_STYLE = Style(
    [
        ("pointer", "fg:#00d7ff bold"),
        ("highlighted", "noinherit bg:#005f87 fg:#ffffff bold"),
        ("qmark", "fg:#5fafd7 bold"),
        ("question", "bold"),
        ("instruction", "fg:#808080"),
        ("text", "fg:#d0d0d0"),
    ]
)


def clear_tty() -> None:
    """Clear screen and move cursor home (each interactive menu starts clean)."""
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
