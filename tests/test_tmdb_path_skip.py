"""parse_tmdb_tag_from_path: skip-resolve detection for tagged library paths."""

from __future__ import annotations

import unittest
from pathlib import Path

from unittest.mock import MagicMock

from titleforge.plex_paths import parse_tmdb_tag_from_path
from titleforge.resolve import PlanContext, resolve_path


class TestParseTmdbTagFromPath(unittest.TestCase):
    def test_movie_under_movies(self) -> None:
        p = Path("/Users/x/Plex/Movies/The Matrix (1999) {tmdb-603}/The Matrix (1999).mkv")
        self.assertEqual(parse_tmdb_tag_from_path(p), (603, "movie"))

    def test_tv_under_series(self) -> None:
        p = Path("/Users/x/Plex/Series/Snowfall {tmdb-789}/Season 01/Snowfall - S01E01 - Pilot.mp4")
        self.assertEqual(parse_tmdb_tag_from_path(p), (789, "tv"))

    def test_first_segment_with_tag_wins(self) -> None:
        p = Path("/Plex/Movies/A {tmdb-1}/B {tmdb-2}/file.mkv")
        self.assertEqual(parse_tmdb_tag_from_path(p), (1, "movie"))

    def test_neither_movies_nor_series_returns_none(self) -> None:
        p = Path("/downloads/foo {tmdb-99}/bar.mkv")
        self.assertIsNone(parse_tmdb_tag_from_path(p))

    def test_both_movies_and_series_in_path_returns_none(self) -> None:
        p = Path("/weird/Movies/backups/Series/foo {tmdb-1}/x.mkv")
        self.assertIsNone(parse_tmdb_tag_from_path(p))

    def test_no_tmdb_tag_returns_none(self) -> None:
        p = Path("/Plex/Movies/Foo (2000)/Foo (2000).mkv")
        self.assertIsNone(parse_tmdb_tag_from_path(p))

    def test_legacy_bracket_tag_still_detected(self) -> None:
        p = Path("/Plex/Movies/Old [tmdb-7]/Old (1999).mkv")
        self.assertEqual(parse_tmdb_tag_from_path(p), (7, "movie"))


class TestResolvePathSkipsTagged(unittest.TestCase):
    def test_resolve_skips_without_calling_tmdb(self) -> None:
        p = Path("/Plex/Movies/Tagged {tmdb-42}/Tagged (2000).mkv")
        ctx = PlanContext(all_files=[p])
        tmdb = MagicMock()
        entry = resolve_path(p, Path("/out"), tmdb, ctx, ignore_tmdb=False)
        self.assertEqual(entry.kind, "skipped")
        self.assertIsNone(entry.dest)
        self.assertEqual(entry.tmdb_movie_id, 42)
        tmdb.movie_detail.assert_not_called()
        tmdb.search_movie.assert_not_called()


if __name__ == "__main__":
    unittest.main()
