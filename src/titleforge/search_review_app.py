"""Phase 1.5 search-review TUI.

One row per entity (top-level folder under ``--input``, or a loose file).
Confidence-sorted: low rows pin to the top in red, medium in yellow, high in
green at the bottom. Edit (`e`) opens a Textual modal with the same Tab-toggle
search-type cycle that ``prompt_search_with_type`` uses outside the TUI — but
implemented as a ``ModalScreen`` so it stays inside Textual's event loop (mixing
prompt_toolkit's ``asyncio.run()`` with Textual's running loop crashes).

Mirrors ``review_app.py``'s style and idioms: plain ``DataTable``, ``Footer``
for binding hints, ``Text`` cells so brace tags in titles render literally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label

from titleforge.models import (
    ConfidenceLevel,
    EntityLabel,
    PlanEntry,
    RenamePlan,
)
from titleforge.plex_paths import build_episode_dest, build_movie_dest
from titleforge.prompt_ui import SEARCH_TYPE_CYCLE, SearchType, _SEARCH_TYPE_LABEL
from titleforge.tmdb_client import TmdbClient
from titleforge.tmdb_errors import TmdbAuthError


_CONF_COLOR: dict[ConfidenceLevel, str] = {
    "low": "red",
    "medium": "yellow",
    "high": "green",
}


def _conf_cell(level: ConfidenceLevel) -> Text:
    return Text(level.upper(), style=f"bold {_CONF_COLOR[level]}")


def _match_cell(label: EntityLabel) -> Text:
    """`Title (Year) {tmdb-id}` if bound; otherwise an italic `— no match —`."""
    if label.kind == "skipped" or label.tmdb_id is None:
        return Text("— no match —", style="italic dim")
    y = f" ({label.year})" if label.year else ""
    return Text(f"{label.title}{y} {{tmdb-{label.tmdb_id}}}")


def _kind_cell(label: EntityLabel) -> Text:
    return Text(label.kind.upper())


class SearchEditModal(ModalScreen["tuple[SearchType, str] | None"]):
    """Edit a row's TMDB search: text input + Tab-cycled search type.

    Returns ``(search_type, query)`` via :meth:`ModalScreen.dismiss` or ``None``
    on cancel / empty submit. Stays entirely inside Textual's event loop —
    replaces an older ``with self.suspend(): prompt_search_with_type(...)``
    block that crashed with ``asyncio.run() cannot be called from a running
    event loop`` (prompt_toolkit's session.prompt() spawns its own asyncio
    loop, which collides with the loop Textual is already running).
    """

    BINDINGS = [
        Binding("tab", "cycle_type", show=False),
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, message: str, default: str, initial_type: SearchType) -> None:
        super().__init__()
        self._message = message
        self._default = default
        self._state = SEARCH_TYPE_CYCLE.index(initial_type)

    def compose(self) -> ComposeResult:
        yield Label(self._message)
        yield Label(self._type_label(), id="search_type_label")
        yield Input(value=self._default, id="search_input")
        with Horizontal(classes="button_row"):
            yield Button("Search", variant="primary", id="search_btn")
            yield Button("Cancel", id="cancel_btn")

    def _type_label(self) -> str:
        label = _SEARCH_TYPE_LABEL[SEARCH_TYPE_CYCLE[self._state]]
        return f"[{label}]  (Tab: switch type)"

    def action_cycle_type(self) -> None:
        self._state = (self._state + 1) % len(SEARCH_TYPE_CYCLE)
        self.query_one("#search_type_label", Label).update(self._type_label())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "search_btn":
            self._submit(self.query_one("#search_input", Input).value)
        else:
            self.dismiss(None)

    def _submit(self, raw: str) -> None:
        q = raw.strip()
        if not q:
            self.dismiss(None)
            return
        self.dismiss((SEARCH_TYPE_CYCLE[self._state], q))


class SearchReviewApp(App[str]):
    """Phase 1.5 search-review.

    Outcome strings: ``"proceed"`` (move on to Phase 2), ``"cancel"`` (abort).
    """

    CSS = """
    DataTable { height: 1fr; }
    #help { height: auto; margin: 1; }
    #counts { height: auto; margin: 0 1; color: $text-muted; }
    """

    BINDINGS = [
        Binding("a", "proceed", "Approve all", show=True),
        Binding("enter", "proceed", "Approve all", show=False),
        Binding("e", "edit", "Edit pick", show=True),
        Binding("space", "next_candidate", "Next candidate", show=True),
        Binding("s", "skip_row", "Skip entity", show=True),
        Binding("c", "cancel", "Cancel", show=True),
        Binding("q", "cancel", "Quit", show=False),
    ]

    def __init__(
        self,
        plan: RenamePlan,
        output_root: Path,
        tmdb: TmdbClient,
    ) -> None:
        super().__init__()
        self.plan = plan
        self.output_root = output_root.resolve()
        self.tmdb = tmdb
        self._done: str = "cancel"
        # Track which stored-candidate index each row is currently showing so
        # `space` cycles forward through `label.candidates`.
        self._candidate_idx: dict[Path, int] = {}

    def compose(self) -> ComposeResult:
        yield Label(
            "[bold]Phase 1.5 — review TMDB matches[/] | "
            "[b]a[/]/[b]Enter[/] Approve  "
            "[b]e[/] Edit  "
            "[b]space[/] Next candidate  "
            "[b]s[/] Skip  "
            "[b]c[/]/[b]q[/] Cancel",
            id="help",
        )
        yield Label("", id="counts")
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(
            "Source folder",
            "Kind",
            "TMDB match",
            "Files",
            "Confidence",
            "Why",
        )
        self._refresh_table()

    # ------------------------------------------------------------------- view

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for lb in self.plan.labels:
            table.add_row(
                Text(lb.display_name),
                _kind_cell(lb),
                _match_cell(lb),
                Text(str(lb.file_count)),
                _conf_cell(lb.confidence),
                Text(lb.reason, style="dim"),
            )
        counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
        for lb in self.plan.labels:
            counts[lb.confidence] += 1
        self.query_one("#counts", Label).update(
            f"{counts['high']} high · {counts['medium']} medium · "
            f"[bold red]{counts['low']} low[/]"
        )

    def _selected_index(self) -> int | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        coord = table.cursor_coordinate
        if coord is None:
            return 0
        return int(coord.row)

    def _selected_label(self) -> EntityLabel | None:
        i = self._selected_index()
        if i is None or i < 0 or i >= len(self.plan.labels):
            return None
        return self.plan.labels[i]

    # ---------------------------------------------------------------- actions

    def action_proceed(self) -> None:
        self._done = "proceed"
        self.exit("proceed")

    def action_cancel(self) -> None:
        self._done = "cancel"
        self.exit("cancel")

    def action_skip_row(self) -> None:
        lb = self._selected_label()
        if lb is None:
            return
        lb.kind = "skipped"
        lb.tmdb_id = None
        lb.confidence = "high"
        lb.reason = "user-skipped"
        for entry in self._entries_for(lb):
            entry.kind = "skipped"
            entry.dest = None
            entry.tmdb_movie_id = None
            entry.tmdb_tv_id = None
        self._refresh_table()

    def action_next_candidate(self) -> None:
        """Cycle through stored TMDB candidates for the selected row.

        ``EntityLabel.candidates`` holds the top-N results from the original
        search (max 15); pressing space rolls the row forward to the next one.
        Rebuilds dest paths for every PlanEntry under the entity.
        """
        lb = self._selected_label()
        if lb is None or not lb.candidates:
            return
        idx = self._candidate_idx.get(lb.key, 0)
        idx = (idx + 1) % len(lb.candidates)
        self._candidate_idx[lb.key] = idx
        self._apply_candidate(lb, lb.candidates[idx], reason=f"cycled to candidate {idx + 1}/{len(lb.candidates)}")
        self._refresh_table()

    def action_edit(self) -> None:
        """Open the Tab-toggle search modal for the selected row.

        Pushes a :class:`SearchEditModal` and dispatches the result through
        :meth:`_after_edit`. Staying inside the Textual loop avoids the
        asyncio-running-loop crash from mixing prompt_toolkit's session.prompt()
        with Textual.
        """
        lb = self._selected_label()
        if lb is None:
            return
        initial_type: SearchType = (
            "movie" if lb.kind == "movie" else ("tv" if lb.kind == "tv" else "both")
        )
        default_query = lb.title or lb.display_name
        self.push_screen(
            SearchEditModal(
                f"Edit search for [{lb.display_name}]:",
                default_query,
                initial_type,
            ),
            lambda res: self._after_edit(lb, res),
        )

    def _after_edit(
        self, lb: EntityLabel, res: "tuple[SearchType, str] | None"
    ) -> None:
        """Apply the user's edited search result to the label + its plan entries."""
        if res is None:
            return
        # Imported lazily to avoid a circular import at module load.
        from titleforge.resolve import _manual_dispatch, PlanContext

        search_type, query = res
        # Build a one-off context just for this edit; we don't want the temporary
        # search to leak into series_by_root for unrelated paths.
        entries = self._entries_for(lb)
        if not entries:
            return
        sample_src = entries[0].src
        tmp_ctx = PlanContext(all_files=[e.src for e in entries])
        try:
            new_entry = _manual_dispatch(
                search_type=search_type,
                query=query,
                year=lb.year,
                path=sample_src,
                output_root=self.output_root,
                tmdb=self.tmdb,
                ctx=tmp_ctx,
            )
        except TmdbAuthError as e:
            self.notify(f"TMDB auth error: {e}", severity="error", timeout=10)
            return
        new_lb_kind = (
            "movie"
            if new_entry.kind == "movie"
            else ("tv" if new_entry.kind == "episode" or new_entry.kind == "extra" else "skipped")
        )
        new_tmdb_id = new_entry.tmdb_movie_id or new_entry.tmdb_tv_id
        if new_tmdb_id is None:
            self.notify("No TMDB match found.", severity="warning", timeout=6)
            return
        pf = tmp_ctx.per_file_label.get(sample_src)
        if pf is not None:
            lb.title = pf.title
            lb.year = pf.year
            lb.confidence = pf.confidence
            lb.reason = pf.reason
            lb.candidates = pf.candidates
        else:
            lb.title = query
            lb.confidence = "high"
            lb.reason = "manual edit"
        lb.kind = new_lb_kind
        lb.tmdb_id = new_tmdb_id
        self._candidate_idx.pop(lb.key, None)
        self._rebuild_entries_for_label(lb)
        self._refresh_table()

    # --------------------------------------------------------------- helpers

    def _entries_for(self, label: EntityLabel) -> list[PlanEntry]:
        return [e for e in self.plan.entries if e.entity_key == label.key]

    def _apply_candidate(
        self, label: EntityLabel, candidate: dict[str, Any], *, reason: str
    ) -> None:
        """Apply a stored TMDB candidate row to the label and its PlanEntries.

        Used by the space-bar "cycle candidate" action. The candidate dict is a
        raw TMDB search result (movie, tv, or tagged dual).
        """
        # Tagged-dual candidates are ("movie"|"tv", row) tuples.
        kind = label.kind
        row = candidate
        if isinstance(candidate, tuple) and len(candidate) == 2 and isinstance(candidate[0], str):
            kind = candidate[0]
            row = candidate[1]
        tmdb_id = int(row.get("id", 0)) or None
        if tmdb_id is None:
            return
        if kind == "movie":
            label.title = row.get("title") or row.get("original_title") or label.title
            rd = row.get("release_date") or ""
            label.year = int(rd[:4]) if len(rd) >= 4 and rd[:4].isdigit() else None
        else:
            label.title = row.get("name") or row.get("original_name") or label.title
            fd = row.get("first_air_date") or ""
            label.year = int(fd[:4]) if len(fd) >= 4 and fd[:4].isdigit() else None
        label.kind = "movie" if kind == "movie" else "tv"
        label.tmdb_id = tmdb_id
        label.confidence = "medium"  # user cycled — no longer "auto"
        label.reason = reason
        self._rebuild_entries_for_label(label)

    def _rebuild_entries_for_label(self, label: EntityLabel) -> None:
        """Recompute dest paths for every PlanEntry under this label.

        Keeps the file's existing kind/season/episode metadata where possible —
        only swaps the TMDB id and title in. Falls back to dest=None when the
        new kind doesn't match the old (e.g. movie ↔ episode swap; the user
        will land in the next-candidate cycle or re-edit).
        """
        if label.tmdb_id is None:
            for entry in self._entries_for(label):
                entry.dest = None
            return
        for entry in self._entries_for(label):
            if label.kind == "movie":
                entry.kind = "movie"
                entry.tmdb_movie_id = label.tmdb_id
                entry.tmdb_tv_id = None
                entry.dest = build_movie_dest(
                    self.output_root,
                    label.title,
                    label.year,
                    entry.src,
                    tmdb_movie_id=label.tmdb_id,
                )
            elif label.kind == "tv":
                entry.tmdb_tv_id = label.tmdb_id
                entry.tmdb_movie_id = None
                if entry.kind == "episode" and entry.season is not None and entry.episode is not None:
                    # Keep the old episode/extra dest shape; just swap the show id.
                    entry.dest = build_episode_dest(
                        self.output_root,
                        label.title,
                        entry.season,
                        entry.episode,
                        entry.src.stem,
                        entry.src,
                        tmdb_tv_id=label.tmdb_id,
                    )
                else:
                    # Extras and unparsed files: leave the existing dest path
                    # alone if it was computed, else null it out. The user can
                    # edit further or skip from here.
                    if entry.dest is None:
                        continue
            else:
                entry.kind = "skipped"
                entry.dest = None

    @property
    def outcome(self) -> str:
        return self._done


def run_search_review(plan: RenamePlan, output_root: Path, tmdb: TmdbClient) -> str:
    """Return "proceed" or "cancel"."""
    if not plan.labels:
        return "proceed"
    app = SearchReviewApp(plan, output_root, tmdb)
    result = app.run()
    return result or app.outcome
