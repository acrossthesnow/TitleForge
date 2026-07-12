"""Regression tests for prefix-first query extraction, the TV retry ladder, and
filename-prefix consensus — locked to the real-world layouts from the
Static Shock / Batman Beyond inbox that produced zero TMDB matches."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

from titleforge.classify import series_prefix_from_stem
from titleforge.normalize import title_prefix, trim_stranded_separators
from titleforge.resolve import PlanContext, prepare_pack_tv_resolve, resolve_path

SS_ROOT = "STATIC SHOCK (2000-2004) - Complete ANIMATED TV Series, S01-S04 - 1080p HMax Web-DL x264"
BB_ROOT = (
    "BATMAN BEYOND (1999-2014) - Complete TV Series, Season 1,2,3 S01-S03, Movie, "
    "Crossovers - 1080p BluRay x264"
)


class TestSeriesPrefixFromStem(unittest.TestCase):
    def test_series_name_is_text_before_marker(self) -> None:
        self.assertEqual(
            series_prefix_from_stem(
                "STATIC SHOCK - S01 E01 - Shock to the System (1080p - HMax Web-DL)"
            ),
            "STATIC SHOCK",
        )

    def test_leading_enumeration_stripped(self) -> None:
        self.assertEqual(
            series_prefix_from_stem("1. The ZETA Project - S01 E08 - Shadows (2001 - 480p DVDRip)"),
            "The ZETA Project",
        )
        self.assertEqual(
            series_prefix_from_stem(
                "2. Static Shock - S04 E01 - Future Shock (2004 - 480p DCU Web-DL)"
            ),
            "Static Shock",
        )

    def test_nxnn_marker(self) -> None:
        self.assertEqual(series_prefix_from_stem("Show 1x02 Title"), "Show")

    def test_year_paren_stripped_from_prefix(self) -> None:
        self.assertEqual(
            series_prefix_from_stem("Firefly (2002) - S01E12 - The Message (1080p BluRay)"),
            "Firefly",
        )

    def test_no_marker_or_empty_prefix_returns_none(self) -> None:
        self.assertIsNone(series_prefix_from_stem("Some Movie (2010)"))
        self.assertIsNone(series_prefix_from_stem("S01E01 - Pilot"))


class TestTitlePrefix(unittest.TestCase):
    def test_static_shock_pack_root(self) -> None:
        self.assertEqual(title_prefix(SS_ROOT), "STATIC SHOCK")

    def test_batman_beyond_pack_root(self) -> None:
        self.assertEqual(title_prefix(BB_ROOT), "BATMAN BEYOND")

    def test_prefixed_season_folder(self) -> None:
        self.assertEqual(
            title_prefix("BATMAN BEYOND - SEASON 1 (1999) - 1080p BluRay x264"),
            "BATMAN BEYOND",
        )

    def test_junk_only_names_yield_empty(self) -> None:
        self.assertEqual(title_prefix("SEASON 1 (2000-2001)"), "")
        self.assertEqual(title_prefix("S01"), "")

    def test_scene_style_dotted_folder(self) -> None:
        self.assertEqual(title_prefix("Pantheon.S01.1080p.HIDI.WEB-DL.x265"), "Pantheon")

    def test_clean_names_pass_through(self) -> None:
        self.assertEqual(title_prefix("The Wire"), "The Wire")

    def test_year_then_season_hints(self) -> None:
        self.assertEqual(title_prefix("Firefly (2002) Season 1 S01 (1080p BluRay x265)"), "Firefly")

    def test_trim_stranded_separators(self) -> None:
        self.assertEqual(trim_stranded_separators("STATIC SHOCK - - "), "STATIC SHOCK")
        self.assertEqual(
            trim_stranded_separators("STATIC SHOCK - ANIMATED TV Series, - -"),
            "STATIC SHOCK - ANIMATED TV Series",
        )


def _ss_row() -> dict:
    return {"id": 2355, "name": "Static Shock", "first_air_date": "2000-09-23", "overview": ""}


class TestStaticShockPackBinds(unittest.TestCase):
    """The decorated pack root must bind on the first rung: folder title prefix
    'STATIC SHOCK' + year 2000 — the exact inbox that previously searched for
    'STATIC SHOCK - ANIMATED TV Series, - -' and got nothing."""

    def test_pack_binds_high_confidence_first_rung(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ss = input_root / SS_ROOT
            f1 = ss / "SEASON 1 (2000-2001)" / (
                "STATIC SHOCK - S01 E01 - Shock to the System (1080p - HMax Web-DL).mp4"
            )
            f2 = ss / "SEASON 1 (2000-2001)" / (
                "STATIC SHOCK - S01 E02 - Aftershock (1080p - HMax Web-DL).mp4"
            )
            f3 = ss / "SEASON 4 (2004)" / (
                "STATIC SHOCK - S04 E01 - Future Shock (1080p - HMax Web-DL).mp4"
            )
            for f in (f1, f2, f3):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.side_effect = lambda q, year=None: (
                [_ss_row()] if q.strip().lower() == "static shock" else []
            )
            tmdb.tv_detail.return_value = {"name": "Static Shock", "first_air_date": "2000-09-23"}
            tmdb.tv_season.side_effect = lambda tv_id, season: {
                1: {"episodes": [
                    {"episode_number": 1, "name": "Shock to the System"},
                    {"episode_number": 2, "name": "Aftershock"},
                ]},
                4: {"episodes": [{"episode_number": 1, "name": "Future Shock"}]},
            }[season]

            ctx = PlanContext(all_files=[f1, f2, f3], input_root=input_root)
            with contextlib.redirect_stderr(io.StringIO()):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)

            binding = ctx.entity_packs.get(ss.resolve())
            self.assertIsNotNone(binding, "pack did not bind")
            assert binding is not None
            self.assertEqual(binding.tmdb_tv_id, 2355)
            self.assertEqual(binding.confidence, "high")
            # First (and only) search: folder title prefix + year — no ladder.
            self.assertEqual(tmdb.search_tv.call_args_list, [call("STATIC SHOCK", 2000)])

            out = Path(td) / "out"
            with contextlib.redirect_stderr(io.StringIO()):
                entries = [resolve_path(f, out, tmdb, ctx, ignore_tmdb=False) for f in (f1, f2, f3)]
            for e in entries:
                self.assertEqual(e.kind, "episode")
                self.assertEqual(e.tmdb_tv_id, 2355)
            self.assertEqual(tmdb.search_tv.call_count, 1)


class TestCrossoversResolvePerFile(unittest.TestCase):
    """A mixed-series crossovers folder ('Season 4 e Crossovers' → query
    'e Crossovers') must not bind as one show; each file falls back to its own
    filename-derived series prefix on the retry ladder, capped at medium."""

    def test_each_file_matches_its_own_series_via_retry(self) -> None:
        shows = {
            "the zeta project": {
                "id": 2085, "name": "The Zeta Project", "first_air_date": "2001-01-27",
            },
            "static shock": {
                "id": 2355, "name": "Static Shock", "first_air_date": "2000-09-23",
            },
            "justice league unlimited": {
                "id": 2251, "name": "Justice League Unlimited", "first_air_date": "2004-07-31",
            },
        }
        seasons = {
            (2085, 1): [{"episode_number": 8, "name": "Shadows"}],
            (2355, 4): [{"episode_number": 1, "name": "Future Shock"}],
            (2251, 3): [{"episode_number": 12, "name": "The Once and Future Thing, Part 1"}],
        }
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            xover = input_root / "Season 4 e Crossovers"
            files = [
                xover / "1. The ZETA Project - S01 E08 - Shadows (2001 - 480p DVDRip).mp4",
                xover / "2. Static Shock - S04 E01 - Future Shock (2004 - 480p DCU Web-DL).mp4",
                xover / (
                    "3. Justice League Unlimited - S03 E12 - The Once and Future Thing, "
                    "Part 1 (2005 - 1080p BluRay).mp4"
                ),
            ]
            xover.mkdir(parents=True)
            for f in files:
                f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.side_effect = lambda q, year=None: (
                [{**shows[q.strip().lower()], "overview": ""}] if q.strip().lower() in shows else []
            )
            tmdb.tv_detail.side_effect = lambda tv_id: next(
                s for s in shows.values() if s["id"] == tv_id
            )
            tmdb.tv_season.side_effect = lambda tv_id, season: {
                "episodes": seasons[(tv_id, season)]
            }

            ctx = PlanContext(all_files=list(files), input_root=input_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)
            # Mixed prefixes → no consensus → the junk folder must NOT bind.
            self.assertEqual(ctx.entity_packs, {})

            out = Path(td) / "out"
            expected = [(2085, "The Zeta Project"), (2355, "Static Shock"),
                        (2251, "Justice League Unlimited")]
            with contextlib.redirect_stderr(err):
                for f, (tv_id, _name) in zip(files, expected):
                    entry = resolve_path(f, out, tmdb, ctx, ignore_tmdb=False)
                    self.assertEqual(entry.kind, "episode", f"{f.name}: {entry.note}")
                    self.assertEqual(entry.tmdb_tv_id, tv_id)
                    label = ctx.per_file_label[f]
                    self.assertEqual(label.confidence, "medium")
                    self.assertIn("matched via retry query", label.reason)
            notices = err.getvalue()
            self.assertIn("No TMDB TV results for 'e Crossovers'", notices)
            self.assertIn("retrying with 'The ZETA Project'", notices)


class TestConsensusFallbackBindsPack(unittest.TestCase):
    """When the folder name is unsearchable garbage, the consensus filename
    prefix binds the pack on rung 2 — capped at medium confidence."""

    def test_consensus_rung_binds_medium(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            junk = input_root / "Random Uploader Junk Folder"
            f1 = junk / "Firefly - S01E01 - Serenity.mkv"
            f2 = junk / "Firefly - S01E02 - The Train Job.mkv"
            junk.mkdir(parents=True)
            f1.write_bytes(b"")
            f2.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.side_effect = lambda q, year=None: (
                [{"id": 1437, "name": "Firefly", "first_air_date": "2002-09-20", "overview": ""}]
                if q.strip().lower() == "firefly"
                else []
            )
            tmdb.tv_detail.return_value = {"name": "Firefly", "first_air_date": "2002-09-20"}
            tmdb.tv_season.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "Serenity"},
                    {"episode_number": 2, "name": "The Train Job"},
                ]
            }

            ctx = PlanContext(all_files=[f1, f2], input_root=input_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)

            binding = ctx.entity_packs.get(junk.resolve())
            self.assertIsNotNone(binding, "consensus rung did not bind the pack")
            assert binding is not None
            self.assertEqual(binding.tmdb_tv_id, 1437)
            self.assertEqual(binding.confidence, "medium")
            self.assertIn("filename consensus", binding.reason)
            self.assertEqual(
                tmdb.search_tv.call_args_list,
                [call("Random Uploader Junk Folder", None), call("Firefly", None)],
            )
            self.assertIn("retrying with 'Firefly'", err.getvalue())

            out = Path(td) / "out"
            with contextlib.redirect_stderr(io.StringIO()):
                e1 = resolve_path(f1, out, tmdb, ctx, ignore_tmdb=False)
                e2 = resolve_path(f2, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(e1.kind, "episode")
            self.assertEqual(e2.kind, "episode")
            # Members reuse the binding — no per-file re-search.
            self.assertEqual(tmdb.search_tv.call_count, 2)


if __name__ == "__main__":
    unittest.main()
