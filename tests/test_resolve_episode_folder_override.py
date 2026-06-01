"""resolve_episode folder-name override strips S\\d / Season hints.

Regression for the inbox case where ``Pantheon.S01.HIDI.WEB-DL.…/Pantheon.S01E01.…mkv``
searched TMDB as ``"Pantheon S01 HIDI"`` (with the season token still in the
query) and returned zero hits. The override has to mirror
prepare_pack_tv_resolve and drop ``S\\d`` / ``Season \\d`` from the folder name
before handing it to TMDB.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.resolve import PlanContext, resolve_episode


class TestResolveEpisodeFolderOverride(unittest.TestCase):
    def test_folder_name_season_token_dropped_from_query(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            pack = input_root / "Pantheon.S01.HIDI.WEB-DL.AAC2.0.H.264-NTb"
            f1 = pack / "Pantheon.S01E01.1080p.HIDI.WEB-DL.AAC2.0.H.264-NTb.mkv"
            f2 = pack / "Pantheon.S01E02.1080p.HIDI.WEB-DL.AAC2.0.H.264-NTb.mkv"
            for f in (f1, f2):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = MagicMock()
            # Make the search return one Pantheon-shaped hit so the resolver
            # gets all the way through finalize and exercises the cleaned-query
            # path we actually care about.
            tmdb.search_tv.return_value = [
                {"id": 195339, "name": "Pantheon", "first_air_date": "2022-09-01"}
            ]
            tmdb.tv_detail.return_value = {
                "name": "Pantheon",
                "first_air_date": "2022-09-01",
            }
            tmdb.tv_season.return_value = {
                "episodes": [{"episode_number": 1, "name": "Reflections"}]
            }

            ctx = PlanContext(all_files=[f1, f2], input_root=input_root)
            resolve_episode(f1, Path(td) / "out", tmdb, ctx)

            # The actual call MUST be just the title, not "Pantheon S01 HIDI".
            queries = [call.args[0] for call in tmdb.search_tv.call_args_list]
            self.assertTrue(queries, "expected at least one search_tv call")
            for q in queries:
                self.assertNotIn("S01", q, f"S01 leaked into query: {q!r}")
                self.assertNotIn(
                    "Season", q, f"Season hint leaked into query: {q!r}"
                )
            # And the cleaned query must still be sensible — the show title.
            self.assertIn("Pantheon", queries[0])
            # HIDI must be gone too — that's the language-token strip in
            # normalize._RESOLUTION; this test pins them together.
            self.assertNotIn("HIDI", queries[0])

    def test_complete_series_folder_collapsed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            pack = input_root / "Some.Show.Complete.Series.1080p.WEB-DL.x264"
            f = pack / "Some.Show.S03E07.Episode.1080p.WEB-DL.x264-Grp.mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 1, "name": "Some Show", "first_air_date": "2018-01-01"}
            ]
            tmdb.tv_detail.return_value = {"name": "Some Show"}
            tmdb.tv_season.return_value = {
                "episodes": [{"episode_number": 7, "name": "An Episode"}]
            }

            ctx = PlanContext(all_files=[f], input_root=input_root)
            resolve_episode(f, Path(td) / "out", tmdb, ctx)

            queries = [call.args[0] for call in tmdb.search_tv.call_args_list]
            for q in queries:
                self.assertNotIn("Complete", q, f"Complete leaked into query: {q!r}")
                self.assertNotIn("Series", q, f"Series leaked into query: {q!r}")


if __name__ == "__main__":
    unittest.main()
