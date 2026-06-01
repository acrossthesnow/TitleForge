"""_rebuild_entries_for_label re-finalizes previously-skipped TV entries.

Regression for the Phase 1.5 edit flow where a low-confidence skipped row
(no TMDB match in Phase 1) was edited by the user. The label cell flipped to
the new TMDB id, but the underlying PlanEntry kept ``kind="skipped"`` and
``dest=None`` because the rebuild only touched entries whose existing kind was
already ``"episode"``. End result: the user sees the edit "succeed" in the
TUI but the file is silently dropped in Phase 2.

Mocks Textual at import time so the App class is constructible without a
running event loop — we only exercise the helper.
"""

from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Importing search_review_app pulls in Textual; the helper we care about only
# touches App.tmdb / App.output_root / App.plan. Stub the App base class out
# before import so we don't need a live event loop for these unit tests.
_textual_app_stub = types.ModuleType("textual.app")


class _StubApp:
    def __init__(self, *args, **kwargs) -> None:  # pragma: no cover - trivial
        pass


_textual_app_stub.App = _StubApp
_textual_app_stub.ComposeResult = object
sys.modules.setdefault("textual.app", _textual_app_stub)

from titleforge.models import EntityLabel, PlanEntry, RenamePlan  # noqa: E402
from titleforge.search_review_app import SearchReviewApp  # noqa: E402


def _mk_app(plan: RenamePlan, out: Path, tmdb: MagicMock) -> SearchReviewApp:
    app = SearchReviewApp.__new__(SearchReviewApp)
    app.plan = plan
    app.output_root = out.resolve()
    app.tmdb = tmdb
    app._candidate_idx = {}
    return app


class TestRebuildEntriesAfterEdit(unittest.TestCase):
    def test_previously_skipped_tv_entry_gets_real_dest_after_edit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            f = td / "Pantheon.S01E01.1080p.HIDI.WEB-DL.mkv"
            f.write_bytes(b"")
            entry = PlanEntry(
                src=f,
                dest=None,
                kind="skipped",
                tmdb_movie_id=None,
                tmdb_tv_id=None,
                season=None,
                episode=None,
                note="No TV results",
                entity_key=f.resolve(),
            )
            label = EntityLabel(
                key=f.resolve(),
                display_name=f.name,
                kind="tv",
                tmdb_id=195339,
                title="Pantheon",
                year=2022,
                confidence="high",
                reason="manual edit",
                file_count=1,
                candidates=[],
            )
            plan = RenamePlan(entries=[entry], labels=[label])

            tmdb = MagicMock()
            tmdb.tv_season.return_value = {
                "episodes": [{"episode_number": 1, "name": "Reflections"}]
            }

            app = _mk_app(plan, td / "out", tmdb)
            app._rebuild_entries_for_label(label)

            # The entry must now be a real episode with a real dest path.
            self.assertEqual(entry.kind, "episode")
            self.assertIsNotNone(entry.dest, "skipped entry should be rebuilt")
            self.assertEqual(entry.tmdb_tv_id, 195339)
            self.assertEqual(entry.season, 1)
            self.assertEqual(entry.episode, 1)
            self.assertIn("Pantheon", str(entry.dest))
            self.assertIn("Season 01", str(entry.dest))

    def test_existing_episode_entry_dest_updates_to_new_show(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            f = td / "Show.S02E05.mkv"
            f.write_bytes(b"")
            entry = PlanEntry(
                src=f,
                dest=td / "out" / "Series" / "Old (2010) {tmdb-999}" / "Season 02" / "Old - S02E05 - Old.mkv",
                kind="episode",
                tmdb_movie_id=None,
                tmdb_tv_id=999,
                season=2,
                episode=5,
                note="",
                entity_key=f.resolve(),
            )
            label = EntityLabel(
                key=f.resolve(),
                display_name=f.name,
                kind="tv",
                tmdb_id=12345,
                title="New Show",
                year=2020,
                confidence="high",
                reason="manual edit",
                file_count=1,
                candidates=[],
            )
            plan = RenamePlan(entries=[entry], labels=[label])

            tmdb = MagicMock()
            tmdb.tv_season.return_value = {
                "episodes": [{"episode_number": 5, "name": "New Episode"}]
            }

            app = _mk_app(plan, td / "out", tmdb)
            app._rebuild_entries_for_label(label)

            self.assertEqual(entry.tmdb_tv_id, 12345)
            self.assertIn("{tmdb-12345}", str(entry.dest))
            self.assertIn("New Show", str(entry.dest))
            self.assertNotIn("{tmdb-999}", str(entry.dest))

    def test_movie_label_rebuilds_dest_for_skipped_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            f = td / "phantom.menace.1999.4k-kc.mkv"
            f.write_bytes(b"")
            entry = PlanEntry(
                src=f,
                dest=None,
                kind="skipped",
                note="No movie results",
                entity_key=f.resolve(),
            )
            label = EntityLabel(
                key=f.resolve(),
                display_name=f.name,
                kind="movie",
                tmdb_id=1893,
                title="Star Wars: The Phantom Menace",
                year=1999,
                confidence="high",
                reason="manual edit",
                file_count=1,
                candidates=[],
            )
            plan = RenamePlan(entries=[entry], labels=[label])

            app = _mk_app(plan, td / "out", MagicMock())
            app._rebuild_entries_for_label(label)

            self.assertEqual(entry.kind, "movie")
            self.assertIsNotNone(entry.dest)
            self.assertEqual(entry.tmdb_movie_id, 1893)
            self.assertIn("{tmdb-1893}", str(entry.dest))


if __name__ == "__main__":
    unittest.main()
