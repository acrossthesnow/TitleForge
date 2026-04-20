from __future__ import annotations

import shutil
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label

from rich.text import Text

from titleforge.models import PlanEntry, RenamePlan


class PathEditModal(ModalScreen[str | None]):
    """Edit destination path for one plan row."""

    def __init__(self, dest_default: str) -> None:
        super().__init__()
        self._dest_default = dest_default

    def compose(self) -> ComposeResult:
        yield Label("Destination (absolute path, or relative to output root):")
        yield Input(value=self._dest_default, id="dest_input")
        with Horizontal(classes="button_row"):
            yield Button("Save", variant="primary", id="save_btn")
            yield Button("Cancel", id="cancel_btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "save_btn":
            inp = self.query_one("#dest_input", Input)
            self.dismiss(inp.value.strip() or None)
        elif bid == "cancel_btn":
            self.dismiss(None)


class ReviewApp(App[None]):
    """Phase 2: scrollable plan, Proceed / Cancel / Modify."""

    CSS = """
    DataTable { height: 1fr; }
    #help { height: auto; margin: 1; }
    .button_row { height: auto; margin-top: 1; }
    """

    BINDINGS = [
        Binding("p", "proceed", "Proceed", show=True),
        Binding("c", "cancel", "Cancel", show=True),
        Binding("m", "modify", "Modify dest", show=True),
        Binding("q", "cancel", "Quit", show=False),
    ]

    def __init__(self, plan: RenamePlan, output_root: Path) -> None:
        super().__init__()
        self.plan = plan
        self.output_root = output_root.resolve()
        self._done: str = "cancel"

    def compose(self) -> ComposeResult:
        yield Label(
            "[bold]Review rename plan[/] — arrows scroll | [b]p[/] Proceed  [b]c[/]/[b]q[/] Cancel  [b]m[/] Modify selected row",
            id="help",
        )
        yield DataTable(cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Source", "Destination")
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for e in self.plan.entries:
            dest_s = str(e.dest) if e.dest else ("— skipped —" if e.kind == "skipped" else "—")
            # DataTable parses str cells as Rich markup; `{tmdb-…}` / brackets in paths must be literal.
            table.add_row(Text(str(e.src)), Text(dest_s))

    def _selected_row_index(self) -> int | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        coord = table.cursor_coordinate
        if coord is None:
            return 0
        return int(coord.row)

    def action_modify(self) -> None:
        idx = self._selected_row_index()
        if idx is None or idx < 0 or idx >= len(self.plan.entries):
            return
        entry = self.plan.entries[idx]
        default = str(entry.dest) if entry.dest else ""
        self.push_screen(PathEditModal(default), lambda r: self._apply_edit(idx, r))

    def _apply_edit(self, idx: int, raw: str | None) -> None:
        if raw is None:
            return
        entry = self.plan.entries[idx]
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (self.output_root / p).resolve()
        entry.dest = p
        self._refresh_table()

    def action_cancel(self) -> None:
        self._done = "cancel"
        self.exit()

    def _check_duplicate_dests(self) -> str | None:
        seen: dict[Path, Path] = {}
        for e in self.plan.entries:
            if e.dest is None or e.kind == "skipped":
                continue
            d = e.dest.resolve()
            if d in seen and seen[d] != e.src.resolve():
                return f"Duplicate destination:\n{d}\n{seen[d]}\n{e.src}"
            seen[d] = e.src.resolve()
        return None

    def action_proceed(self) -> None:
        dup = self._check_duplicate_dests()
        if dup:
            self.notify(dup, severity="error", timeout=10)
            return
        for e in self.plan.entries:
            if e.kind == "skipped" or e.dest is None:
                continue
            dest = e.dest.resolve()
            if dest.exists() and dest.samefile(e.src.resolve()):
                continue
            if dest.exists():
                self.notify(f"Refusing overwrite existing file:\n{dest}", severity="error", timeout=10)
                return

        for e in self.plan.entries:
            if e.kind == "skipped" or e.dest is None:
                continue
            dest = e.dest.resolve()
            if dest.exists() and e.src.resolve() == dest:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(e.src), str(dest))

        self._done = "proceed"
        self.exit()

    @property
    def outcome(self) -> str:
        return self._done


def run_review(plan: RenamePlan, output_root: Path) -> str:
    app = ReviewApp(plan, output_root)
    app.run()
    return app.outcome
