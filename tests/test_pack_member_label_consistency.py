"""Files #2..#N in a same-series pack get a real TV label, not the catch-all.

Regression for the reported `Supernatural.S01E02.…mkv` row whose Phase 1.5
edit modal pre-populated with the raw filename (including `.mkv`) and then
TMDB-searched for it literally — zero hits.

Cause: when pack-TV bind fails but per-file ``resolve_episode`` succeeds for
file #1, it caches ``series_by_root[root]``. File #2 reuses the cache,
short-circuits the search inside ``resolve_episode``, and lands directly in
``_finalize_episode``. Pre-fix, ``_finalize_episode`` did NOT write to
``ctx.per_file_label`` on its happy path, so the file fell through to the
``_build_entity_labels`` catch-all, which stamped ``key.name`` (filename with
extension) as the label's ``title``. That `title` then drove the search-edit
modal's default query.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.resolve import (
    PlanContext,
    _build_entity_labels,
    resolve_episode,
)


class TestPackMemberLabelConsistency(unittest.TestCase):
    def test_file_2_in_pack_gets_series_label_not_filename(self) -> None:
        """Two sibling files in a same-root pack. File #1's resolve_episode
        does the TMDB search and caches ``series_by_root[root]``; file #2
        skips the search and lands directly in ``_finalize_episode``. Both
        files must end up with a TV label whose title is the show name —
        never the raw filename with extension."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            pack = (
                input_root
                / "SUPERNATURAL SEASON 1-12 COMPLETE [2005-2017] Blu-Ray H265 HEVC-Adyen"
            )
            f1 = pack / "Supernatural.S01E01.1080p.BluRay.H265.Eng-Adyen.mkv"
            f2 = pack / "Supernatural.S01E02.1080p.BluRay.H265.Eng-Adyen.mkv"
            for f in (f1, f2):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 1622, "name": "Supernatural", "first_air_date": "2005-09-13"}
            ]
            tmdb.tv_detail.return_value = {
                "name": "Supernatural",
                "first_air_date": "2005-09-13",
            }
            tmdb.tv_season.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "Pilot"},
                    {"episode_number": 2, "name": "Wendigo"},
                ]
            }

            ctx = PlanContext(all_files=[f1, f2], input_root=input_root)
            e1 = resolve_episode(f1, Path(td) / "out", tmdb, ctx)
            e2 = resolve_episode(f2, Path(td) / "out", tmdb, ctx)
            # entity_key is what build_plan would set for per-file entries.
            e1.entity_key = f1.resolve()
            e2.entity_key = f2.resolve()

            # Both entries resolved as real episodes.
            self.assertEqual(e1.kind, "episode")
            self.assertEqual(e2.kind, "episode")

            # File #1 hit the search → search_by_root cached → file #2 skipped it.
            self.assertEqual(tmdb.search_tv.call_count, 1)

            # The labels built from these entries must NOT fall to the catch-all.
            # Pre-fix: file #2's label was kind="skipped" with title equal to
            # the raw filename "Supernatural.S01E02.…mkv".
            labels = _build_entity_labels([e1, e2], ctx)
            by_key = {lb.key: lb for lb in labels}
            lb2 = by_key[f2.resolve()]
            self.assertEqual(lb2.kind, "tv", "file #2 must be a TV label, not catch-all skipped")
            self.assertEqual(lb2.tmdb_id, 1622)
            self.assertEqual(lb2.title, "Supernatural")
            self.assertNotIn(".mkv", lb2.title)
            self.assertNotIn("BluRay", lb2.title)
            self.assertNotIn("S01E02", lb2.title)
            self.assertEqual(lb2.confidence, "high")
            self.assertEqual(lb2.reason, "series binding")
            # File #2 must also carry the show year so it renders as
            # "Supernatural (2005)" — the user-reported inconsistency was
            # file #1 having "(2005)" and file #2 missing it.
            self.assertEqual(lb2.year, 2005, "file #2 must inherit the year via series_year_by_tv_id")
            lb1 = by_key[f1.resolve()]
            self.assertEqual(lb1.year, 2005)


if __name__ == "__main__":
    unittest.main()
