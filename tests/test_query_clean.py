"""Tests for stem parsing: title + year for TMDB search."""

from __future__ import annotations

import unittest

from titleforge.query_clean import clean_stem_for_search


class TestParenYear(unittest.TestCase):
    def test_atlantis_paren_year_stripped_from_title(self) -> None:
        stem = "Atlantis - The Lost Empire (2001)"
        c = clean_stem_for_search(stem)
        self.assertEqual(c.year, 2001)
        self.assertEqual(c.title, "Atlantis - The Lost Empire")
        self.assertNotIn("2001)", c.title)
        self.assertEqual(c.raw_stem, stem)

    def test_year_prefix_hyphen(self) -> None:
        c = clean_stem_for_search("2001 - A Space Odyssey")
        self.assertEqual(c.year, 2001)
        self.assertEqual(c.title, "A Space Odyssey")

    def test_year_suffix_hyphen(self) -> None:
        c = clean_stem_for_search("Some Show - 2020")
        self.assertEqual(c.year, 2020)
        self.assertEqual(c.title, "Some Show")


class TestParenYearWithTrailingBrackets(unittest.TestCase):
    def test_serenity_with_trailing_quality_bracket(self) -> None:
        c = clean_stem_for_search("Serenity (2005)  [2160p x265 10bit FS90 Joy]")
        self.assertEqual(c.year, 2005)
        self.assertEqual(c.title, "Serenity")


class TestDotYearReleaseTail(unittest.TestCase):
    def test_the_martian_dot_year_drops_release_tail(self) -> None:
        c = clean_stem_for_search("The.Martian.2015.EXTENDED.2160p.UHD.BluRay.x265-TERMiNAL")
        self.assertEqual(c.year, 2015)
        # Release tail (1080p/BluRay/group/etc.) must not leak into the title.
        self.assertEqual(c.title.lower(), "the martian")

    def test_final_fantasy_dot_year_drops_yify_noise(self) -> None:
        c = clean_stem_for_search(
            "Final.Fantasy.The.Spirits.Within.2001.1080p.BrRip.x264.BOKIUTOX.YIFY"
        )
        self.assertEqual(c.year, 2001)
        self.assertNotIn("BOKIUTOX", c.title)
        self.assertNotIn("YIFY", c.title)
        self.assertEqual(c.title.lower(), "final fantasy the spirits within")

    def test_jurassic_world_dominion_with_group_suffix(self) -> None:
        c = clean_stem_for_search(
            "Jurassic.World.Dominion.2022.HDR.EXTENDED.CUT.2160p.UHD.Blu-ray.HEVC.DTS-HD.MA.7.1-EVO[TGx]"
        )
        self.assertEqual(c.year, 2022)
        self.assertEqual(c.title.lower(), "jurassic world dominion")

    def test_dot_year_without_release_tail_is_not_taken(self) -> None:
        # `Show.2020.S01E01` — the "rest" is just S01E01 (no release token).
        # Year extraction via _DOT_YEAR should NOT fire here; the stem keeps its
        # full text (resolution will fall back to _ANY_PAREN_YEAR which also misses).
        c = clean_stem_for_search("Show.2020.S01E01")
        # No release tail means year should not be extracted by _DOT_YEAR; ensure
        # the title is untouched up to that point (no stripping of meaningful text).
        self.assertIsNone(c.year)


class TestAnyParenYearFallback(unittest.TestCase):
    def test_firefly_messy_folder_extracts_year_mid_string(self) -> None:
        c = clean_stem_for_search(
            "Firefly (2002) Season 1 S01 (1080p BluRay x265 HEVC 10bit AAC Silence)"
        )
        self.assertEqual(c.year, 2002)


class TestSceneAndSourceCleanup(unittest.TestCase):
    """Regression coverage for the failing-inbox cases: scene group, streaming
    source tag, and pack-range cleanup must not leave junk in the TMDB query."""

    def test_sons_of_anarchy_pack_folder(self) -> None:
        c = clean_stem_for_search(
            "Sons.of.Anarchy.S01.1080p.AMZN.WEBRip.DDP5.1.x265-SiGMA[rartv]"
        )
        # S01 survives here — the pack-TV resolver strips season markers downstream.
        # AMZN / WEBRip / DDP5.1 / x265 / -SiGMA / [rartv] must all be gone.
        self.assertEqual(c.title, "Sons of Anarchy S01")
        self.assertIsNone(c.year)

    def test_samurai_jack_pack_folder(self) -> None:
        c = clean_stem_for_search(
            "Samurai.Jack.S01.1080p.BluRay.x264-pcroland[rartv]"
        )
        self.assertEqual(c.title, "Samurai Jack S01")
        self.assertIsNone(c.year)

    def test_star_trek_enterprise_pack_range(self) -> None:
        c = clean_stem_for_search("Star Trek Enterprise Season 1 to 4 Mp4 1080p")
        self.assertEqual(c.title, "Star Trek Enterprise")
        self.assertIsNone(c.year)

    def test_dot_year_movie_with_streaming_source_in_tail(self) -> None:
        # _RELEASE_TAIL now lists streaming abbreviations so the dot-year
        # heuristic still recognises tails like `.AMZN.WEB-DL.x265-Group`.
        c = clean_stem_for_search("Movie.2020.AMZN.WEB-DL.x265-Group")
        self.assertEqual(c.year, 2020)
        self.assertEqual(c.title, "Movie")


if __name__ == "__main__":
    unittest.main()
