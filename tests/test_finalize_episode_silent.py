"""_finalize_episode never prompts in Phase 1.

Three paths to lock in:
- TMDB has the episode title → use it (high-confidence, unchanged behavior).
- TMDB has the season but no title for the episode → derive from filename
  (e.g. `Firefly (2002) - S01E12 - The Message (1080p ...)` → "The Message"),
  mark medium-confidence so the user sees it in Phase 1.5.
- No SxxEyy in the filename → low-confidence skipped label, no prompt.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.resolve import (
    PlanContext,
    _derive_episode_title_from_stem,
    _finalize_episode,
)


class TestDeriveEpisodeTitleFromStem(unittest.TestCase):
    def test_firefly_format(self) -> None:
        stem = "Firefly (2002) - S01E12 - The Message (1080p BluRay x265 Silence)"
        self.assertEqual(_derive_episode_title_from_stem(stem), "The Message")

    def test_dot_format(self) -> None:
        stem = "Show.Name.S03E05.Episode.Title.1080p.BluRay.x264-GROUP"
        # After stripping release tokens, what's left should be `Episode Title`.
        derived = _derive_episode_title_from_stem(stem) or ""
        self.assertIn("Episode", derived)
        self.assertIn("Title", derived)
        self.assertNotIn("1080p", derived)
        self.assertNotIn("GROUP", derived)

    def test_no_sxxeyy_returns_none(self) -> None:
        self.assertIsNone(_derive_episode_title_from_stem("Random.Movie.Title.2020"))

    def test_empty_after_sxxeyy_returns_none(self) -> None:
        self.assertIsNone(_derive_episode_title_from_stem("Show - S01E01"))


class TestFinalizeEpisodeSilent(unittest.TestCase):
    def test_tmdb_has_title_uses_it(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Show"
            f = show / "Show - S01E01 - Pilot.mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.tv_season.return_value = {
                "episodes": [{"episode_number": 1, "name": "Pilot (TMDB)"}],
            }

            ctx = PlanContext(all_files=[f], input_root=input_root)
            entry = _finalize_episode(f, Path(td) / "out", tmdb, ctx, tv_id=42, series_name="Show")
            self.assertEqual(entry.kind, "episode")
            self.assertIsNotNone(entry.dest)
            self.assertIn("Pilot (TMDB)", entry.dest.name)
            # No medium-confidence label, since title came from TMDB.
            self.assertNotIn(f, ctx.per_file_label)

    def test_no_tmdb_title_derives_from_filename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Firefly (2002) Season 1 S01"
            f = show / "Firefly (2002) - S01E12 - The Message (1080p BluRay x265 Silence).mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            # Season has episodes but no name for E12 (simulating TMDB hiccup).
            tmdb.tv_season.return_value = {
                "episodes": [{"episode_number": 12, "name": None}],
            }

            ctx = PlanContext(all_files=[f], input_root=input_root)
            entry = _finalize_episode(
                f, Path(td) / "out", tmdb, ctx, tv_id=1437, series_name="Firefly"
            )
            self.assertEqual(entry.kind, "episode")
            self.assertIsNotNone(entry.dest)
            # Derived title from filename appears in the destination filename.
            self.assertIn("The Message", entry.dest.name)
            # Flagged medium-confidence so the user sees it in Phase 1.5.
            self.assertIn(f, ctx.per_file_label)
            label = ctx.per_file_label[f]
            self.assertEqual(label.confidence, "medium")
            self.assertIn("derived from filename", label.reason)

    def test_missing_sxxeyy_marks_low_no_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Show"
            # No SxxEyy in this filename.
            f = show / "Random Featurette Without Episode Marker.mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()

            ctx = PlanContext(all_files=[f], input_root=input_root)
            entry = _finalize_episode(
                f, Path(td) / "out", tmdb, ctx, tv_id=42, series_name="Show"
            )
            self.assertEqual(entry.kind, "skipped")
            self.assertEqual(entry.note, "missing SxxEyy")
            label = ctx.per_file_label[f]
            self.assertEqual(label.confidence, "low")
            self.assertEqual(label.reason, "missing SxxEyy")
            # No TMDB call was made because we bailed before fetching the season.
            tmdb.tv_season.assert_not_called()


if __name__ == "__main__":
    unittest.main()
