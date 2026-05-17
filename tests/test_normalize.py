"""strip_release_info: scene group, source tag, container, pack-range stripping.

These cases all started as TMDB-search failures on a real inbox where the
cleaner left junk (`-pcroland`, `AMZN`, `Mp4`, `to 4`) in the query.
"""

from __future__ import annotations

import unittest

from titleforge.normalize import strip_release_info


class TestSceneGroupSuffix(unittest.TestCase):
    def test_pcroland_after_x264_dropped_for_pack_folder(self) -> None:
        s = strip_release_info(
            "Samurai.Jack.S01.1080p.BluRay.x264-pcroland[rartv]"
        )
        # SxxEyy / S01 is intentionally preserved by strip_release_info — the
        # downstream pack-TV resolver strips it separately. Scene group must go.
        self.assertEqual(s, "Samurai Jack S01")

    def test_sigma_after_x265_dropped_with_amzn_and_ddp(self) -> None:
        s = strip_release_info(
            "Sons.of.Anarchy.S01.1080p.AMZN.WEBRip.DDP5.1.x265-SiGMA[rartv]"
        )
        # AMZN (streaming), WEBRip (source), DDP5.1 (audio), x265 (codec),
        # `-SiGMA` (scene group), `[rartv]` (release tracker tag) all stripped.
        self.assertEqual(s, "Sons of Anarchy S01")

    def test_chained_scene_tail_peeled_iteratively(self) -> None:
        # `-Group -Extra` (after release tokens) is a synthetic case — the
        # iteration matters when there's already a leading space before the
        # first dash.
        s = strip_release_info("Movie 1080p -RARBG -EXTRA")
        self.assertEqual(s, "Movie")

    def test_spider_man_hyphen_preserved(self) -> None:
        # No release tokens before the hyphen → no whitespace before it → the
        # scene-tail sweep won't fire and the title's own hyphen stays.
        s = strip_release_info("Spider-Man (2002)")
        self.assertEqual(s, "Spider-Man")


class TestPackRange(unittest.TestCase):
    def test_season_1_to_4_removed_as_unit(self) -> None:
        s = strip_release_info("Star Trek Enterprise Season 1 to 4 Mp4 1080p")
        self.assertEqual(s, "Star Trek Enterprise")

    def test_seasons_with_dash_range(self) -> None:
        s = strip_release_info("Some Show Seasons 1-4 1080p")
        self.assertEqual(s, "Some Show")

    def test_complete_series_removed(self) -> None:
        s = strip_release_info("Some Show Complete Series 1080p BluRay")
        self.assertEqual(s, "Some Show")


class TestStreamingSources(unittest.TestCase):
    def test_amzn_web_dl_dropped(self) -> None:
        s = strip_release_info("Title.2020.AMZN.WEB-DL.x265")
        # _DOT_YEAR isn't applied here (that's query_clean.py's job); the
        # year survives in strip_release_info output. We only assert the
        # streaming/codec tokens are gone.
        self.assertNotIn("AMZN", s)
        self.assertNotIn("WEB", s)
        self.assertNotIn("x265", s)

    def test_dsnp_atvp_pcok_stripped(self) -> None:
        for tag in ("DSNP", "ATVP", "PCOK", "HMAX", "HULU", "STARZ", "CRAVE", "STAN"):
            s = strip_release_info(f"Title.2020.{tag}.WEB-DL.x265")
            self.assertNotIn(tag, s, f"{tag} should be stripped, got {s!r}")

    def test_short_ambiguous_streaming_tags_left_alone(self) -> None:
        # Real movie titles like "MA" (2019) and "IT" (2017) must NOT be
        # mistaken for streaming abbreviations. Two-letter codes are skipped
        # from _RESOLUTION precisely to avoid this.
        self.assertEqual(strip_release_info("MA"), "MA")
        self.assertEqual(strip_release_info("IT"), "IT")


class TestContainerToken(unittest.TestCase):
    def test_mp4_inside_folder_name_stripped(self) -> None:
        s = strip_release_info("Star Trek Enterprise Mp4 1080p")
        self.assertNotIn("Mp4", s)
        self.assertNotIn("1080p", s)

    def test_mkv_inside_folder_name_stripped(self) -> None:
        s = strip_release_info("Show Title mkv 1080p")
        self.assertNotIn("mkv", s.lower())


class TestAudioFragments(unittest.TestCase):
    def test_dd5_1_dot_form(self) -> None:
        # `DD.5.1` was being matched as just `DD` by the old regex, leaving
        # orphan `5 1` digits in the title after separator collapse.
        s = strip_release_info("Show.S01E01.1080p.WEB-DL.DD.5.1.H.265-Grp")
        self.assertNotIn("5", s)
        self.assertNotIn("DD", s)
        self.assertNotIn("Grp", s)

    def test_ddp5_1_no_dot(self) -> None:
        s = strip_release_info("Show.S01E01.DDP5.1.x265-Grp")
        self.assertNotIn("DDP", s)

    def test_dd2_0_inline(self) -> None:
        s = strip_release_info("Show.S01E01.DD2.0.x264-Grp")
        self.assertNotIn("DD", s)
        self.assertNotIn("2", s)


class TestPerFileStem(unittest.TestCase):
    def test_samurai_jack_per_file_stem(self) -> None:
        s = strip_release_info(
            "Samurai.Jack.S01E01.1080p.BluRay.DD2.0.x264-pcroland"
        )
        self.assertEqual(s, "Samurai Jack S01E01")

    def test_sons_of_anarchy_per_file_stem_with_pilot(self) -> None:
        s = strip_release_info(
            "Sons.of.Anarchy.S01E01.Pilot.1080p.AMZN.WEB-DL.DD.5.1.H.265-SiGMA"
        )
        # Episode title `Pilot` is part of the stem; the cleaner has no way to
        # know it's not part of the show name. Downstream code (series_query_string)
        # strips the S01E01 marker; the episode title is harmless in a TMDB
        # *show* query because TMDB tolerates extra words.
        self.assertEqual(s, "Sons of Anarchy S01E01 Pilot")


class TestNonAggressivePath(unittest.TestCase):
    def test_non_aggressive_preserves_brackets(self) -> None:
        s = strip_release_info("Show [BluRay] x264-Grp", aggressive=False)
        # Non-aggressive: brackets stay, but codec / scene tail still strip.
        self.assertIn("[", s)
        self.assertNotIn("x264", s)


if __name__ == "__main__":
    unittest.main()
