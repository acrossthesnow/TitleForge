"""Destination-conflict surfacing: duplicate destinations and destinations that
already exist on disk must become their own LOW rows in the Phase 1.5 search
review, instead of hiding inside entity groups until Phase 2's Proceed guard
refuses the whole plan."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.resolve import build_plan

SHOW = {"id": 7, "name": "Show", "first_air_date": "2020-01-01"}


def _stub() -> MagicMock:
    tmdb = MagicMock()
    tmdb.search_tv.side_effect = lambda q, year=None: (
        [dict(SHOW, overview="")] if q.strip().lower() == "show" else []
    )
    tmdb.tv_detail.return_value = dict(SHOW)
    tmdb.tv_season.side_effect = lambda tv_id, season: {
        "episodes": [
            {"episode_number": 1, "name": "Pilot"},
            {"episode_number": 2, "name": "Second"},
        ]
    }
    tmdb.search_movie.return_value = []
    tmdb.find_imdb_movie.return_value = None
    return tmdb


class TestDuplicateDestRows(unittest.TestCase):
    def test_two_sources_one_dest_become_low_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "in"
            f1 = inbox / "GroupA" / "Show - S01E01 - Pilot.mkv"
            f2 = inbox / "GroupB" / "Show.S01E01.Pilot.1080p.WEB.mkv"
            for f in (f1, f2):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                plan = build_plan([f1, f2], Path(td) / "out", _stub(), input_root=inbox)

            dup_labels = [lb for lb in plan.labels if "duplicate destination" in lb.reason]
            self.assertEqual(len(dup_labels), 2)
            for lb in dup_labels:
                self.assertEqual(lb.confidence, "low")
                self.assertEqual(lb.title, "Show")
            # LOW sorts first, so the conflicts lead the review table.
            self.assertEqual(plan.labels[0].confidence, "low")
            self.assertEqual(
                {lb.display_name for lb in dup_labels}, {f1.name, f2.name}
            )
            for e in plan.entries:
                self.assertIn("duplicate destination — also from", e.note)
            self.assertIn("Duplicate destination (2 files):", err.getvalue())

    def test_pack_member_conflict_gets_own_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "in"
            pack = inbox / "Show Complete"
            f1 = pack / "Show - S01E01 - Pilot.mkv"
            f2 = pack / "Show - S01E01 - Pilot (repack).mkv"
            f3 = pack / "Show - S01E02 - Second.mkv"
            pack.mkdir(parents=True)
            for f in (f1, f2, f3):
                f.write_bytes(b"")

            with contextlib.redirect_stderr(io.StringIO()):
                plan = build_plan([f1, f2, f3], Path(td) / "out", _stub(), input_root=inbox)

            dup_labels = [lb for lb in plan.labels if "duplicate destination" in lb.reason]
            self.assertEqual(len(dup_labels), 2, [lb.reason for lb in plan.labels])
            self.assertEqual({lb.display_name for lb in dup_labels}, {f1.name, f2.name})
            for lb in dup_labels:
                self.assertEqual(lb.confidence, "low")
                self.assertEqual(lb.tmdb_id, 7)
            # The pack row survives with only the non-conflicted member.
            pack_label = next(lb for lb in plan.labels if lb.key == pack.resolve())
            self.assertEqual(pack_label.file_count, 1)


class TestExistingDestRow(unittest.TestCase):
    def test_dest_already_on_disk_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            inbox = Path(td) / "in"
            out = Path(td) / "out"
            f1 = inbox / "GroupA" / "Show - S01E01 - Pilot.mkv"
            f1.parent.mkdir(parents=True)
            f1.write_bytes(b"")

            # First pass tells us the destination; materialize it, then re-plan.
            with contextlib.redirect_stderr(io.StringIO()):
                first = build_plan([f1], out, _stub(), input_root=inbox)
            dest = first.entries[0].dest
            assert dest is not None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"already here")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                plan = build_plan([f1], out, _stub(), input_root=inbox)

            (label,) = [lb for lb in plan.labels if lb.key == f1.resolve()]
            self.assertEqual(label.confidence, "low")
            self.assertIn("destination already exists on disk", label.reason)
            self.assertIn("destination already exists on disk", plan.entries[0].note)
            self.assertIn("Destination already exists on disk:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
