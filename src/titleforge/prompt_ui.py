"""TTY clear + questionary list styling (no raw ANSI inside questionary messages)."""

from __future__ import annotations

import sys
from html import escape as _html_escape
from typing import Literal

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
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


SearchType = Literal["movie", "tv", "both"]
SEARCH_TYPE_CYCLE: tuple[SearchType, ...] = ("movie", "tv", "both")
_SEARCH_TYPE_LABEL: dict[SearchType, str] = {
    "movie": "Movie",
    "tv": "TV",
    "both": "Movie+TV",
}


def next_search_type(current: SearchType) -> SearchType:
    """Tab order for the search-type toggle."""
    return SEARCH_TYPE_CYCLE[(SEARCH_TYPE_CYCLE.index(current) + 1) % len(SEARCH_TYPE_CYCLE)]


def prompt_search_with_type(
    message: str,
    default: str = "",
    initial_type: SearchType = "both",
) -> tuple[SearchType, str] | None:
    """
    Text prompt with Tab cycling the search type (Movie / TV / Movie+TV).

    Returns ``(search_type, query)`` or ``None`` if the user cancels or submits an empty
    string. Ctrl-C propagates as ``KeyboardInterrupt`` so the CLI's top-level handler can
    abort cleanly, matching the existing ``questionary.text`` behavior.
    """
    state_idx = [SEARCH_TYPE_CYCLE.index(initial_type)]
    bindings = KeyBindings()

    @bindings.add(Keys.Tab)
    def _toggle(event) -> None:  # type: ignore[no-untyped-def]
        state_idx[0] = (state_idx[0] + 1) % len(SEARCH_TYPE_CYCLE)
        event.app.invalidate()

    def _format_message() -> HTML:
        label = _SEARCH_TYPE_LABEL[SEARCH_TYPE_CYCLE[state_idx[0]]]
        return HTML(
            f"<ansicyan>[{label}]</ansicyan> "
            f"{_html_escape(message)} "
            f"<ansibrightblack>(Tab: switch type)</ansibrightblack> "
        )

    try:
        session: PromptSession[str] = PromptSession()
        text = session.prompt(_format_message, key_bindings=bindings, default=default)
    except EOFError:
        return None
    text = (text or "").strip()
    if not text:
        return None
    return SEARCH_TYPE_CYCLE[state_idx[0]], text
