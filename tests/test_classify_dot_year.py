"""Dot-year filename recognition: classify scene-style names as movies."""

from __future__ import annotations

import unittest
from pathlib import Path

from titleforge.classify import guess_kind, looks_episode, looks_movie


class TestLooksMovieDotYear(unittest.TestCase):
    def test_dot_year_with_release_tokens_is_movie(self) -> None:
        p = Path("The.Martian.2015.EXTENDED.2160p.UHD.BluRay.x265-TERMiNAL.mkv")
        self.assertTrue(looks_movie(p))
        self.assertEqual(guess_kind(p), "movie")

    def test_dot_year_yify_movie(self) -> None:
        p = Path("Final.Fantasy.The.Spirits.Within.2001.1080p.BrRip.x264.BOKIUTOX.YIFY.mp4")
        self.assertTrue(looks_movie(p))

    def test_dot_year_jurassic_world(self) -> None:
        p = Path(
            "Jurassic.World.Dominion.2022.HDR.EXTENDED.CUT.2160p.UHD.Blu-ray.HEVC.DTS-HD.MA.7.1-EVO.mkv"
        )
        self.assertTrue(looks_movie(p))

    def test_dot_year_without_release_tokens_rejected(self) -> None:
        # `Show.2020.S01E01` is not a movie; looks_episode wins first anyway, but the
        # safety net is that _NAME_DOT_YEAR requires a release-tail token after the year.
        p = Path("Show.2020.S01E01.mkv")
        # parse_sxe makes this an episode
        self.assertTrue(looks_episode(p))
        self.assertFalse(looks_movie(p))

    def test_paren_year_with_trailing_quality_bracket_is_movie(self) -> None:
        p = Path("Serenity (2005)  [2160p x265 10bit FS90 Joy].mkv")
        self.assertTrue(looks_movie(p))


if __name__ == "__main__":
    unittest.main()
