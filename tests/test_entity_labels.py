"""build_plan emits one EntityLabel per entity, sorted low→medium→high."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.models import EntityLabel
from titleforge.resolve import build_plan


class TestEntityLabels(unittest.TestCase):
    def test_pack_tv_entity_produces_one_high_confidence_label(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Firefly (2002) Season 1 S01"
            ep1 = show / "Firefly (2002) - S01E01 - Serenity.mkv"
            ep2 = show / "Firefly (2002) - S01E02 - The Train Job.mkv"
            ep1.parent.mkdir(parents=True, exist_ok=True)
            ep1.write_bytes(b"")
            ep2.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 1437, "name": "Firefly", "first_air_date": "2002-09-20", "overview": ""},
            ]
            tmdb.search_movie.return_value = []
            tmdb.tv_detail.return_value = {
                "name": "Firefly",
                "original_name": "Firefly",
                "first_air_date": "2002-09-20",
            }
            tmdb.tv_season.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "Serenity"},
                    {"episode_number": 2, "name": "The Train Job"},
                ],
            }

            plan = build_plan(
                [ep1, ep2], Path(td) / "out", tmdb, input_root=input_root
            )

            self.assertEqual(len(plan.labels), 1)
            lb = plan.labels[0]
            self.assertEqual(lb.kind, "tv")
            self.assertEqual(lb.tmdb_id, 1437)
            self.assertEqual(lb.title, "Firefly")
            self.assertEqual(lb.year, 2002)
            self.assertEqual(lb.file_count, 2)
            # Single TMDB hit → high confidence
            self.assertEqual(lb.confidence, "high")

    def test_no_results_per_file_label_is_low_skipped(self) -> None:
        """A loose top-level file with no TMDB hits → low-confidence skipped label."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            f = input_root / "Unknown.Random.Title.mkv"
            f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = []
            tmdb.search_movie.return_value = []

            plan = build_plan(
                [f], Path(td) / "out", tmdb, input_root=input_root
            )

            self.assertEqual(len(plan.labels), 1)
            lb = plan.labels[0]
            self.assertEqual(lb.kind, "skipped")
            self.assertEqual(lb.confidence, "low")
            self.assertIn("no TMDB", lb.reason.lower())

    def test_labels_sorted_low_first(self) -> None:
        """Low rows pin to top; high rows at bottom."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            good = input_root / "The.Martian.2015.1080p.BluRay.x264.mkv"
            bad = input_root / "Zzz.Random.Stuff.mkv"
            good.write_bytes(b"")
            bad.write_bytes(b"")

            tmdb = MagicMock()

            def search_movie(q: str, year: int | None = None):
                if "martian" in q.lower():
                    return [
                        {
                            "id": 286217,
                            "title": "The Martian",
                            "release_date": "2015-09-30",
                            "overview": "",
                        }
                    ]
                return []

            tmdb.search_movie.side_effect = search_movie
            tmdb.search_tv.return_value = []
            tmdb.movie_detail.return_value = {
                "id": 286217,
                "title": "The Martian",
                "release_date": "2015-09-30",
            }

            plan = build_plan(
                [good, bad], Path(td) / "out", tmdb, input_root=input_root
            )

            # Two labels — both loose files.
            self.assertEqual(len(plan.labels), 2)
            # Low confidence sorts to the top.
            self.assertEqual(plan.labels[0].confidence, "low")
            self.assertEqual(plan.labels[1].confidence, "high")


class TestSilentResolver(unittest.TestCase):
    def test_no_tmdb_hits_does_not_prompt(self) -> None:
        """Phase 1 must not call questionary.text or prompt_search_with_type.

        We catch that by making the tmdb mock return nothing and asserting
        build_plan completes without raising (it would raise if it tried to
        run an unmocked questionary prompt in this non-TTY harness)."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            f = input_root / "Some.Mystery.File.With.No.Year.mkv"
            f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = []
            tmdb.search_movie.return_value = []

            plan = build_plan(
                [f], Path(td) / "out", tmdb, input_root=input_root
            )
            self.assertEqual(len(plan.labels), 1)
            self.assertEqual(plan.labels[0].confidence, "low")


if __name__ == "__main__":
    unittest.main()
