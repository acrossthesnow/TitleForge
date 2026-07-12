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

    def test_title_parens_survive_year_groups_dropped(self) -> None:
        # "(Unlimited)" is part of the show name; "(2002)" is a year group.
        self.assertEqual(
            series_prefix_from_stem(
                "JUSTICE LEAGUE (Unlimited) - S03 E12 - The Once and Future Thing, "
                "Weird Western Tales (1080p - BluRay)"
            ),
            "JUSTICE LEAGUE (Unlimited)",
        )


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

    def test_lettered_subfolders(self) -> None:
        # Real-world uploader style: "a."…"f." enumerated subfolders. These must
        # never survive as one-letter TMDB queries ("a" → Q&A, "b" → Plan B).
        self.assertEqual(title_prefix("a. Season 1 (1999)"), "")
        self.assertEqual(title_prefix("b. Season 2 (1999-2000)"), "")
        self.assertEqual(title_prefix("c. Season 3 (2000-01)"), "")
        self.assertEqual(title_prefix("d. Movie (2000)"), "Movie")
        self.assertEqual(title_prefix("e. Crossovers (2001-05)"), "Crossovers")
        self.assertEqual(title_prefix("TRUE Ending (2005)"), "TRUE Ending")

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


def _stub_tv(shows: dict[str, dict], seasons: dict[tuple[int, int], list[dict]]) -> MagicMock:
    """MagicMock TMDB client keyed on exact (case-insensitive) TV queries."""
    tmdb = MagicMock()
    tmdb.search_tv.side_effect = lambda q, year=None: (
        [{**shows[q.strip().lower()], "overview": ""}] if q.strip().lower() in shows else []
    )
    tmdb.tv_detail.side_effect = lambda tv_id: next(
        dict(s) for s in shows.values() if s["id"] == tv_id
    )
    tmdb.tv_season.side_effect = lambda tv_id, season: {"episodes": seasons[(tv_id, season)]}
    return tmdb


def _tv_queries(tmdb: MagicMock) -> list[str]:
    return [c.args[0].strip().lower() for c in tmdb.search_tv.call_args_list]


class TestCrossoversResolvePerFile(unittest.TestCase):
    """The real crossovers folder ('e. Crossovers (2001-05)') holds episodes of
    three different shows. The census must veto the pack bind, and each file
    must search as its own series — the folder-derived query ('Crossovers')
    must never run, even though TMDB has a show it would match."""

    def test_each_file_matches_its_own_series(self) -> None:
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
            # Decoy: would wrongly bind all five files if the folder query ran.
            "crossovers": {"id": 999, "name": "Crossovers", "first_air_date": "2010-01-01"},
        }
        seasons = {
            (2085, 1): [{"episode_number": 8, "name": "Shadows"}],
            (2355, 4): [{"episode_number": 1, "name": "Future Shock"}],
            (2251, 3): [{"episode_number": 12, "name": "The Once and Future Thing, Part 1"}],
        }
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            xover = input_root / "e. Crossovers (2001-05)"
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

            tmdb = _stub_tv(shows, seasons)
            ctx = PlanContext(all_files=list(files), input_root=input_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)
            # Mixed prefixes → census veto, without a single pack search.
            self.assertEqual(ctx.entity_packs, {})
            self.assertIn("3 different series", err.getvalue())

            out = Path(td) / "out"
            expected = [2085, 2355, 2251]
            with contextlib.redirect_stderr(err):
                for f, tv_id in zip(files, expected):
                    entry = resolve_path(f, out, tmdb, ctx, ignore_tmdb=False)
                    self.assertEqual(entry.kind, "episode", f"{f.name}: {entry.note}")
                    self.assertEqual(entry.tmdb_tv_id, tv_id)
                    # Own filename is the primary signal in a mixed folder — a
                    # single hit on it is a high-confidence match, not a retry.
                    self.assertEqual(ctx.per_file_label[f].confidence, "high")
            queries = _tv_queries(tmdb)
            self.assertNotIn("crossovers", queries)
            self.assertNotIn("e crossovers", queries)
            # A mixed folder must never receive a cached series identity.
            self.assertEqual(ctx.series_by_root, {})


class TestConsensusFrontloadBindsPack(unittest.TestCase):
    """When the folder name has nothing in common with what the member files
    call the show, the filename consensus searches FIRST — the junk folder name
    never reaches TMDB and the bind stays high with the source annotated."""

    def test_consensus_first_when_folder_unrelated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            junk = input_root / "Random Uploader Junk Folder"
            f1 = junk / "Firefly - S01E01 - Serenity.mkv"
            f2 = junk / "Firefly - S01E02 - The Train Job.mkv"
            junk.mkdir(parents=True)
            f1.write_bytes(b"")
            f2.write_bytes(b"")

            tmdb = _stub_tv(
                {"firefly": {"id": 1437, "name": "Firefly", "first_air_date": "2002-09-20"}},
                {(1437, 1): [
                    {"episode_number": 1, "name": "Serenity"},
                    {"episode_number": 2, "name": "The Train Job"},
                ]},
            )

            ctx = PlanContext(all_files=[f1, f2], input_root=input_root)
            with contextlib.redirect_stderr(io.StringIO()):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)

            binding = ctx.entity_packs.get(junk.resolve())
            self.assertIsNotNone(binding, "consensus did not bind the pack")
            assert binding is not None
            self.assertEqual(binding.tmdb_tv_id, 1437)
            self.assertEqual(binding.confidence, "high")
            self.assertIn("query from filename consensus", binding.reason)
            self.assertEqual(tmdb.search_tv.call_args_list, [call("Firefly", None)])

            out = Path(td) / "out"
            with contextlib.redirect_stderr(io.StringIO()):
                e1 = resolve_path(f1, out, tmdb, ctx, ignore_tmdb=False)
                e2 = resolve_path(f2, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(e1.kind, "episode")
            self.assertEqual(e2.kind, "episode")
            # Members reuse the binding — no per-file re-search.
            self.assertEqual(tmdb.search_tv.call_count, 1)


class TestRealBatmanBeyondLayout(unittest.TestCase):
    """The full Batman Beyond pack shape from the real inbox: lettered season
    folders, a movie subfolder (vetoes the pack), and a mixed crossovers
    subfolder. One-letter queries must never be searched — decoy shows for
    "a"/"b"/"c" would otherwise reproduce the Q&A / Plan B / C.I.D. binds."""

    def test_lettered_folders_resolve_by_filename(self) -> None:
        shows = {
            "batman beyond": {"id": 1130, "name": "Batman Beyond", "first_air_date": "1999-01-10"},
            "the zeta project": {
                "id": 2085, "name": "The Zeta Project", "first_air_date": "2001-01-27",
            },
            "static shock": {
                "id": 2355, "name": "Static Shock", "first_air_date": "2000-09-23",
            },
            # Decoys reproducing the real-world garbage binds if ever queried.
            "a": {"id": 7562, "name": "Q&A", "first_air_date": "2008-01-01"},
            "b": {"id": 212055, "name": "Plan B", "first_air_date": "2023-01-01"},
            "c": {"id": 15226, "name": "C.I.D.", "first_air_date": "1998-01-01"},
            "crossovers": {"id": 999, "name": "Crossovers", "first_air_date": "2010-01-01"},
        }
        seasons = {
            (1130, 1): [
                {"episode_number": 1, "name": "Rebirth (1)"},
                {"episode_number": 2, "name": "Rebirth (2)"},
            ],
            (1130, 2): [{"episode_number": 1, "name": "Splicers"}],
            (1130, 3): [{"episode_number": 1, "name": "King's Ransom"}],
            (2085, 1): [{"episode_number": 8, "name": "Shadows"}],
            (2355, 4): [{"episode_number": 1, "name": "Future Shock"}],
        }
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            bb = input_root / BB_ROOT
            s1 = bb / "a. Season 1 (1999)"
            s2 = bb / "b. Season 2 (1999-2000)"
            s3 = bb / "c. Season 3 (2000-01)"
            movie_dir = bb / "d. Movie (2000)"
            xover = bb / "e. Crossovers (2001-05)"
            ep_files = [
                s1 / "Batman BEYOND - S01 E01 - Rebirth, Part 1 of 2 (1080p - BluRay).mp4",
                s1 / "Batman BEYOND - S01 E02 - Rebirth, Part 2 of 2 (1080p - BluRay).mp4",
                s2 / "Batman BEYOND - S02 E01 - Splicers (1080p - BluRay).mp4",
                s3 / "Batman BEYOND - S03 E01 - King's Ransom (1080p - BluRay).mp4",
            ]
            movie_file = movie_dir / "Batman Beyond - Return of The Joker (2000 - 1080p BluRay).mp4"
            xover_files = [
                xover / "1. The ZETA Project - S01 E08 - Shadows (2001 - 480p DVDRip).mp4",
                xover / "2. Static Shock - S04 E01 - Future Shock (2004 - 480p DCU Web-DL).mp4",
            ]
            all_files = ep_files + [movie_file] + xover_files
            for f in all_files:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = _stub_tv(shows, seasons)
            tmdb.search_movie.side_effect = lambda q, year=None: (
                [{"id": 16234, "title": "Batman Beyond: Return of the Joker",
                  "release_date": "2000-12-12"}]
                if "return of the joker" in q.lower()
                else []
            )
            tmdb.movie_detail.return_value = {
                "id": 16234,
                "title": "Batman Beyond: Return of the Joker",
                "release_date": "2000-12-12",
            }
            tmdb.find_imdb_movie.return_value = None

            ctx = PlanContext(all_files=list(all_files), input_root=input_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)
            # The movie file (no SxxEyy) vetoes the single-show pack shape.
            self.assertEqual(ctx.entity_packs, {})

            out = Path(td) / "out"
            with contextlib.redirect_stderr(err):
                entries = {
                    f: resolve_path(f, out, tmdb, ctx, ignore_tmdb=False) for f in all_files
                }

            for f in ep_files:
                self.assertEqual(entries[f].kind, "episode", f"{f.name}: {entries[f].note}")
                self.assertEqual(entries[f].tmdb_tv_id, 1130, f.name)
            self.assertEqual(entries[movie_file].kind, "movie")
            self.assertEqual(entries[movie_file].tmdb_movie_id, 16234)
            self.assertEqual(entries[xover_files[0]].tmdb_tv_id, 2085)
            self.assertEqual(entries[xover_files[1]].tmdb_tv_id, 2355)

            queries = _tv_queries(tmdb)
            for junk_query in ("a", "b", "c", "crossovers", "e crossovers"):
                self.assertNotIn(junk_query, queries, f"junk query {junk_query!r} was searched")
            # One folder-cached search per lettered season folder.
            self.assertEqual(queries.count("batman beyond"), 3)
            self.assertNotIn(xover.resolve(), ctx.series_by_root)


class TestPackMemberSeriesMismatch(unittest.TestCase):
    """A bound pack must not stamp its id onto member files that name a
    different show — the Static Shock pack ships two JLU episodes in
    'TRUE Ending (2005)', which would otherwise collide with the real
    Static Shock S03E12/E13."""

    def test_foreign_members_resolve_as_their_own_series(self) -> None:
        shows = {
            "static shock": {
                "id": 2355, "name": "Static Shock", "first_air_date": "2000-09-23",
            },
            "justice league (unlimited)": {
                "id": 2251, "name": "Justice League Unlimited", "first_air_date": "2004-07-31",
            },
            # Decoy: the folder-name query must lose to the filename consensus.
            "true ending": {"id": 888, "name": "True Ending", "first_air_date": "2015-01-01"},
        }
        seasons = {
            (2355, 1): [
                {"episode_number": 1, "name": "Shock to the System"},
                {"episode_number": 2, "name": "Aftershock"},
                {"episode_number": 3, "name": "The Breed"},
            ],
            (2355, 2): [{"episode_number": 1, "name": "The Big Leagues"}],
            (2355, 4): [{"episode_number": 1, "name": "Future Shock"}],
            (2251, 3): [
                {"episode_number": 12, "name": "The Once and Future Thing, Part One"},
                {"episode_number": 13, "name": "The Once and Future Thing, Part Two"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ss = input_root / SS_ROOT
            ss_files = [
                ss / "Season 1 (2000-01)" / (
                    f"STATIC SHOCK - S01 E{n:02d} - Title (1080p - HMax Web-DL).mp4"
                )
                for n in (1, 2, 3)
            ] + [
                ss / "Season 2 (2002)" / (
                    "STATIC SHOCK - S02 E01 - The Big Leagues (1080p - HMax Web-DL).mp4"
                ),
                ss / "Season 4 (2004)" / (
                    "STATIC SHOCK - S04 E01 - Future Shock (1080p - HMax Web-DL).mp4"
                ),
            ]
            jlu_files = [
                ss / "TRUE Ending (2005)" / (
                    "JUSTICE LEAGUE (Unlimited) - S03 E12 - The Once and Future Thing, "
                    "Weird Western Tales (1080p - BluRay).mp4"
                ),
                ss / "TRUE Ending (2005)" / (
                    "JUSTICE LEAGUE (Unlimited) - S03 E13 - The Once and Future Thing, "
                    "Time Warped (1080p - BluRay).mp4"
                ),
            ]
            all_files = ss_files + jlu_files
            for f in all_files:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = _stub_tv(shows, seasons)
            ctx = PlanContext(all_files=list(all_files), input_root=input_root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                prepare_pack_tv_resolve(ctx, tmdb, input_root)
            binding = ctx.entity_packs.get(ss.resolve())
            self.assertIsNotNone(binding, "Static Shock pack did not bind")
            assert binding is not None
            self.assertEqual(binding.tmdb_tv_id, 2355)
            # 5 of 7 members agree on STATIC SHOCK — dominant, so no mixed veto.
            self.assertEqual(tmdb.search_tv.call_args_list[0], call("STATIC SHOCK", 2000))

            out = Path(td) / "out"
            with contextlib.redirect_stderr(err):
                for f in ss_files:
                    entry = resolve_path(f, out, tmdb, ctx, ignore_tmdb=False)
                    self.assertEqual(entry.tmdb_tv_id, 2355, f.name)
                jlu_entries = [
                    resolve_path(f, out, tmdb, ctx, ignore_tmdb=False) for f in jlu_files
                ]
            for entry in jlu_entries:
                self.assertEqual(entry.kind, "episode", entry.note)
                self.assertEqual(entry.tmdb_tv_id, 2251)
                assert entry.dest is not None
                self.assertIn("Justice League Unlimited {tmdb-2251}", entry.dest.parts)
                self.assertIn("Season 03", entry.dest.parts)

            self.assertIn("names a different series", err.getvalue())
            queries = _tv_queries(tmdb)
            # Consensus outranks the unrelated folder name: the decoy-matching
            # "TRUE Ending" query never runs, and JLU is searched exactly once.
            self.assertNotIn("true ending", queries)
            self.assertEqual(queries.count("justice league (unlimited)"), 1)


if __name__ == "__main__":
    unittest.main()
