"""NFO-resolved movies show up in the search-review table as kind=movie.

Regression for the inbox where each Star Wars file had a sibling NFO with an
IMDb id; resolve_movie matched them via ``/find/{imdb_id}`` and returned a
valid kind=movie PlanEntry — but never set ctx.per_file_label. The downstream
_build_entity_labels then fell through to its catch-all branch and rendered
the row as ``SKIPPED — no match —`` with reason ``"from IMDb tt0120915"``,
which is exactly the user-facing bug.

These tests assert that both NFO branches populate per_file_label and that
_build_entity_labels surfaces a kind=movie label with the IMDb / TMDB reason.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from titleforge.resolve import PlanContext, build_plan, resolve_movie


class TestNfoMovieLabelSurfaces(unittest.TestCase):
    def _mk_video(self, td: Path, name: str) -> Path:
        f = td / name
        f.write_bytes(b"")
        return f

    def test_imdb_nfo_branch_sets_per_file_label_movie(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            f = self._mk_video(td, "phantom.menace.1999.mkv")

            tmdb = MagicMock()
            tmdb.find_imdb_movie.return_value = {
                "id": 1893,
                "title": "Star Wars: The Phantom Menace",
                "release_date": "1999-05-19",
            }
            tmdb.movie_detail.return_value = {
                "id": 1893,
                "title": "Star Wars: The Phantom Menace",
                "release_date": "1999-05-19",
            }

            ctx = PlanContext(all_files=[f])
            with patch(
                "titleforge.resolve.collect_ids_near_video",
                return_value=(120915, None, None),
            ):
                entry = resolve_movie(f, td / "out", tmdb, ctx)

            self.assertEqual(entry.kind, "movie")
            self.assertIsNotNone(entry.dest)
            self.assertEqual(entry.note, "from IMDb tt0120915")

            # And per_file_label MUST be populated so _build_entity_labels
            # generates a movie label, not the SKIPPED catch-all.
            self.assertIn(f, ctx.per_file_label)
            pf = ctx.per_file_label[f]
            self.assertEqual(pf.kind, "movie")
            self.assertEqual(pf.tmdb_id, 1893)
            self.assertEqual(pf.confidence, "high")
            self.assertIn("IMDb", pf.reason)
            self.assertIn("tt0120915", pf.reason)

    def test_tmdb_nfo_branch_sets_per_file_label_movie(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            f = self._mk_video(td, "movie.mkv")

            tmdb = MagicMock()
            tmdb.movie_detail.return_value = {
                "id": 603,
                "title": "The Matrix",
                "release_date": "1999-03-31",
            }

            ctx = PlanContext(all_files=[f])
            with patch(
                "titleforge.resolve.collect_ids_near_video",
                return_value=(None, 603, None),
            ):
                entry = resolve_movie(f, td / "out", tmdb, ctx)

            self.assertEqual(entry.kind, "movie")
            self.assertIsNotNone(entry.dest)
            self.assertEqual(entry.note, "from NFO TMDB id")

            self.assertIn(f, ctx.per_file_label)
            pf = ctx.per_file_label[f]
            self.assertEqual(pf.kind, "movie")
            self.assertEqual(pf.tmdb_id, 603)
            self.assertEqual(pf.confidence, "high")
            self.assertEqual(pf.reason, "from NFO TMDB id")

    def test_build_plan_emits_movie_label_not_skipped_for_nfo_resolved(self) -> None:
        """End-to-end: a single NFO-resolved file should produce a kind=movie
        EntityLabel, not the SKIPPED catch-all that misleadingly shows the NFO
        reason."""
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            f = self._mk_video(td, "phantom.menace.1999.mkv")

            tmdb = MagicMock()
            tmdb.find_imdb_movie.return_value = {
                "id": 1893,
                "title": "Star Wars: The Phantom Menace",
                "release_date": "1999-05-19",
            }
            tmdb.movie_detail.return_value = {
                "id": 1893,
                "title": "Star Wars: The Phantom Menace",
                "release_date": "1999-05-19",
            }

            with patch(
                "titleforge.resolve.collect_ids_near_video",
                return_value=(120915, None, None),
            ):
                plan = build_plan([f], td / "out", tmdb, input_root=td)

            self.assertEqual(len(plan.labels), 1)
            label = plan.labels[0]
            self.assertEqual(label.kind, "movie")
            self.assertEqual(label.tmdb_id, 1893)
            self.assertEqual(label.confidence, "high")
            self.assertIn("IMDb", label.reason)


if __name__ == "__main__":
    unittest.main()
