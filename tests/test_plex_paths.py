"""Path segment sanitization (no trailing invisible / space junk)."""

from __future__ import annotations

import unittest
from pathlib import Path

from titleforge.plex_paths import build_episode_dest, build_movie_dest, sanitize_segment


class TestSanitizeSegment(unittest.TestCase):
    def test_strips_trailing_zero_width_space(self) -> None:
        self.assertEqual(sanitize_segment("hello\u200b"), "hello")

    def test_colon_replace_does_not_leave_zwsp_at_end(self) -> None:
        self.assertEqual(sanitize_segment("Something: \u200b"), "Something -")


class TestBuildDestNoTrailingSpace(unittest.TestCase):
    def test_movie_parts_have_no_trailing_space(self) -> None:
        p = build_movie_dest(Path("/lib"), "Test", 2001, Path("a.mkv"), tmdb_movie_id=1)
        for part in p.parts:
            self.assertFalse(part.endswith(" "), msg=repr(part))

    def test_movie_file_stem_has_no_tmdb_tag(self) -> None:
        p = build_movie_dest(Path("/lib"), "Test", 2001, Path("in.mkv"), tmdb_movie_id=99)
        self.assertEqual(p.name, "Test (2001).mkv")
        self.assertIn("{tmdb-99}", p.parent.name)
        self.assertNotIn("{tmdb-", p.name)

    def test_series_folder_has_no_trailing_space(self) -> None:
        p = build_episode_dest(
            Path("/lib"),
            "Show",
            1,
            1,
            "Pilot",
            Path("x.mkv"),
            tmdb_tv_id=99,
        )
        for part in p.parts:
            self.assertFalse(part.endswith(" "), msg=repr(part))


if __name__ == "__main__":
    unittest.main()
