"""Single-entity pack detection, Featurettes series root, season inference, extras dest."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.pack import (
    content_root,
    entity_roots_under_input,
    first_segments_under,
    infer_season_from_path_ancestors,
    input_entity_for_path,
    is_single_tv_pack,
    season_number_from_dir_name,
)
from titleforge.extra_category import infer_plex_extra_folder
from titleforge.plex_paths import build_season_extra_dest
from titleforge.resolve import PlanContext, prepare_pack_tv_resolve, resolve_path
from titleforge.series_folder import (
    is_season_dir_name,
    parse_alt_season_dir,
    series_group_root,
)


class TestSeriesGroupPastFeaturettes(unittest.TestCase):
    def test_featurettes_season_grandparent_is_show_root(self) -> None:
        base = Path("/media/Mad Men (2007)")
        f = base / "Featurettes" / "Season 2" / "An Era of Style.mkv"
        files = [f]
        root = series_group_root(f, files)
        self.assertEqual(root, base.resolve())


class TestPackHeuristics(unittest.TestCase):
    def test_content_root(self) -> None:
        a = Path("/t/show/Season 1/a.mkv")
        b = Path("/t/show/Featurettes/Season 2/b.mkv")
        self.assertEqual(content_root([a, b], ceiling=Path("/t")), Path("/t/show"))

    def test_content_root_never_above_ceiling(self) -> None:
        a = Path("/t/torrents/show/Season 1/a.mkv")
        b = Path("/t/torrents/show/Featurettes/b.mkv")
        self.assertEqual(content_root([a, b], ceiling=Path("/t/torrents")), Path("/t/torrents/show"))

    def test_is_single_tv_pack_seasons_and_featurettes(self) -> None:
        root = Path("/t/show")
        files = [
            root / "Season 1" / "a.mkv",
            root / "Season 2" / "b.mkv",
            root / "Featurettes" / "Season 1" / "c.mkv",
        ]
        self.assertTrue(is_single_tv_pack(files, root))

    def test_is_single_tv_pack_rejects_two_shows(self) -> None:
        root = Path("/t/torrents")
        files = [
            root / "Mad Men (2007)" / "Season 1" / "a.mkv",
            root / "Avatar (2025)" / "b.mkv",
        ]
        self.assertFalse(is_single_tv_pack(files, root))

    def test_movie_folder_with_sample_subdir_is_not_pack(self) -> None:
        """Regression for The Martian: Sample/ must not tip a movie folder into pack TV."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "The.Martian.2015.EXTENDED.x265-TERMiNAL"
            sample = root / "Sample"
            sample.mkdir(parents=True)
            (sample / "junk.mkv").write_bytes(b"")
            main = root / "The.Martian.2015.EXTENDED.x265-TERMiNAL.mkv"
            main.write_bytes(b"")
            self.assertFalse(is_single_tv_pack([main], root))

    def test_firefly_loose_episodes_and_featurettes_is_pack(self) -> None:
        """Loose SxxEyy episodes at entity root + Featurettes/ should bind as one pack."""
        root = Path("/t/Firefly (2002) Season 1 S01")
        files = [
            root / "Firefly (2002) - S01E01 - Serenity.mkv",
            root / "Firefly (2002) - S01E02 - The Train Job.mkv",
            root / "Featurettes" / "Gag Reel.mkv",
        ]
        self.assertTrue(is_single_tv_pack(files, root))

    def test_movie_collection_is_not_pack(self) -> None:
        """Jurassic Park COLLECTION: multiple loose Title.YYYY.*.mkv files, no SxxEyy."""
        root = Path("/t/Jurassic Park COLLECTION 1993-2015")
        files = [
            root / "Jurassic.Park.1993.REMUX.2160p.mkv",
            root / "Jurassic.Park.III.2001.REMUX.2160p.mkv",
            root / "Jurassic.Park.The.Lost.World.1997.REMUX.2160p.mkv",
        ]
        self.assertFalse(is_single_tv_pack(files, root))

    def test_first_segments_two_shows(self) -> None:
        root = Path("/t/torrents")
        files = [
            root / "Mad Men (2007)" / "Season 1" / "a.mkv",
            root / "Avatar (2025)" / "b.mkv",
        ]
        self.assertEqual(
            first_segments_under(root, files),
            {"Mad Men (2007)", "Avatar (2025)"},
        )

    def test_entity_roots_under_input(self) -> None:
        inp = Path("/in")
        files = [
            inp / "A" / "S01E01.mkv",
            inp / "B" / "x.mkv",
        ]
        self.assertEqual(
            entity_roots_under_input(files, inp),
            [inp / "A", inp / "B"],
        )
        self.assertEqual(input_entity_for_path(inp, inp / "A" / "S01E01.mkv"), inp / "A")


class TestAltSeasonNaming(unittest.TestCase):
    """Avatar uses "Book One - Water" as season-1 folder. Other shows use
    "Volume 2", "Part 3", "Chapter 4". Treat these as season folders so the
    pack binds to one TV identity instead of every episode searching the
    season-folder name as a TMDB query."""

    def test_parse_alt_season_dir_word_numerals(self) -> None:
        self.assertEqual(parse_alt_season_dir("Book One - Water"), 1)
        self.assertEqual(parse_alt_season_dir("Book Two - Earth"), 2)
        self.assertEqual(parse_alt_season_dir("Book Three - Fire"), 3)
        self.assertEqual(parse_alt_season_dir("Book Ten"), 10)

    def test_parse_alt_season_dir_digit_numerals(self) -> None:
        self.assertEqual(parse_alt_season_dir("Volume 2"), 2)
        self.assertEqual(parse_alt_season_dir("Vol. 3"), 3)
        self.assertEqual(parse_alt_season_dir("Vol 4"), 4)
        self.assertEqual(parse_alt_season_dir("Part 5"), 5)
        self.assertEqual(parse_alt_season_dir("Chapter 12"), 12)

    def test_parse_alt_season_dir_rejects_non_season_names(self) -> None:
        self.assertIsNone(parse_alt_season_dir("Book Club"))
        self.assertIsNone(parse_alt_season_dir("Random Folder"))
        self.assertIsNone(parse_alt_season_dir("Season 1"))  # handled by _SEASON_DIR

    def test_is_season_dir_name_matches_both_naming_styles(self) -> None:
        self.assertTrue(is_season_dir_name("Season 1"))
        self.assertTrue(is_season_dir_name("S01"))
        self.assertTrue(is_season_dir_name("Book One - Water"))
        self.assertTrue(is_season_dir_name("Volume 3"))
        self.assertFalse(is_season_dir_name("Featurettes"))

    def test_season_number_from_dir_name_alt_format(self) -> None:
        self.assertEqual(season_number_from_dir_name("Book One - Water"), 1)
        self.assertEqual(season_number_from_dir_name("Volume 3"), 3)
        # Strict "Season N" / "Sn" still works.
        self.assertEqual(season_number_from_dir_name("Season 4"), 4)
        self.assertEqual(season_number_from_dir_name("S05"), 5)

    def test_series_group_root_walks_past_book_n_to_show_root(self) -> None:
        """`series_group_root("Show/Book One - Water/S01E01.mkv")` must
        return the show root, not the "Book One - Water" parent. Pre-fix it
        returned the parent because ep_like >= 2 triggered before any
        season-folder recognition."""
        show = Path("/media/Avatar - The Last Airbender (2005 - 2008) [1080p]")
        files = [
            show / "Book One - Water" / "Avatar - S01E01.mkv",
            show / "Book One - Water" / "Avatar - S01E02.mkv",
            show / "Book Three - Fire" / "Avatar - S03E01.mkv",
        ]
        root = series_group_root(files[0], files)
        self.assertEqual(root, show.resolve())

    def test_is_single_tv_pack_accepts_book_n_layout(self) -> None:
        """The Avatar pack: top-level entity has two Book N children that
        contain SxxEyy episodes. Must bind as a single TV pack."""
        root = Path("/t/Avatar - The Last Airbender (2005 - 2008) [1080p]")
        files = [
            root / "Book One - Water" / "Avatar - The Last Airbender - S01E01.mkv",
            root / "Book One - Water" / "Avatar - The Last Airbender - S01E02.mkv",
            root / "Book Three - Fire" / "Avatar - The Last Airbender - S03E01.mkv",
        ]
        self.assertTrue(is_single_tv_pack(files, root))

    def test_is_single_tv_pack_rejects_part_n_movie_layout(self) -> None:
        """`Movie/Part 1/foo.mkv + Movie/Part 2/bar.mkv` with no SxxEyy is a
        movie split into parts — must NOT be treated as a TV pack just because
        "Part N" matches the alt-season regex."""
        root = Path("/t/Some.Long.Movie.2020")
        files = [
            root / "Part 1" / "movie.cd1.mkv",
            root / "Part 2" / "movie.cd2.mkv",
        ]
        self.assertFalse(is_single_tv_pack(files, root))

    def test_infer_season_from_book_n_ancestor(self) -> None:
        pack = Path("/media/Avatar - The Last Airbender (2005 - 2008) [1080p]")
        p = pack / "Book Three - Fire" / "Avatar - The Last Airbender - S03E04.mkv"
        self.assertEqual(infer_season_from_path_ancestors(p, pack), 3)


class TestPrefixedSeasonFolders(unittest.TestCase):
    """Season folders whose name has the show as a prefix
    (`Revolution (2012) S01`, `The Bear Season 3`) — common scene-pack layout."""

    def test_is_season_dir_name_accepts_prefix(self) -> None:
        self.assertTrue(is_season_dir_name("Revolution (2012) S01"))
        self.assertTrue(is_season_dir_name("The Bear Season 3"))
        self.assertTrue(is_season_dir_name("Firefly (2002) Season 1 S01"))
        # Strict "Season N" / "S01" still work.
        self.assertTrue(is_season_dir_name("Season 1"))
        self.assertTrue(is_season_dir_name("S01"))
        # Non-season names still rejected.
        self.assertFalse(is_season_dir_name("Featurettes"))
        self.assertFalse(is_season_dir_name("Wheatley's Letters"))
        self.assertFalse(is_season_dir_name("Enemies of the State"))

    def test_season_number_from_prefixed_dir(self) -> None:
        self.assertEqual(season_number_from_dir_name("Revolution (2012) S01"), 1)
        self.assertEqual(season_number_from_dir_name("Revolution (2012) S02"), 2)
        self.assertEqual(season_number_from_dir_name("The Bear Season 3"), 3)
        # Bare forms still work.
        self.assertEqual(season_number_from_dir_name("Season 1"), 1)
        self.assertEqual(season_number_from_dir_name("S07"), 7)

    def test_is_single_tv_pack_accepts_prefixed_season_children(self) -> None:
        root = Path("/t/Revolution (2012) S01-S02 (1080p BluRay Celdra)")
        files = [
            root / "Revolution (2012) S01" / "Revolution (2012) - S01E01 - Pilot.mkv",
            root / "Revolution (2012) S01" / "Featurettes" / "Gag Reel.mkv",
            root / "Revolution (2012) S02" / "Revolution (2012) - S02E01 - Born in the USA.mkv",
        ]
        self.assertTrue(is_single_tv_pack(files, root))

    def test_is_single_tv_pack_accepts_specials_arc_subfolder(self) -> None:
        """A subfolder whose files are ALL SxxEyy (Revolution's S00 arc
        `Wheatley's Letters/` and `Enemies of the State/`) doesn't match any
        season/extras regex but should still be pack-accepted."""
        root = Path("/t/Revolution (2012) S01-S02 (1080p BluRay Celdra)")
        files = [
            root / "Revolution (2012) S01" / "Revolution (2012) - S01E01 - Pilot.mkv",
            root / "Revolution (2012) S01" / "Revolution (2012) - S01E02 - Chained Heat.mkv",
            root
            / "Revolution (2012) Wheatley's Letters"
            / "Revolution (2012) - S00E11 - Wheatley's Letters May 7th.mkv",
            root
            / "Revolution (2012) Wheatley's Letters"
            / "Revolution (2012) - S00E12 - Wheatley's Letters August 10th.mkv",
            root
            / "Revolution (2012) Enemies of the State"
            / "Revolution (2012) - S00E17 - Enemies of the State Part 1.mkv",
        ]
        self.assertTrue(is_single_tv_pack(files, root))

    def test_is_single_tv_pack_rejects_arbitrary_movie_at_root(self) -> None:
        """A subfolder with a non-episode file (e.g. a loose movie in a mixed
        top-level dir) must still fail the pack check — the specials-arc
        tolerance requires EVERY file inside the subfolder to have SxxEyy."""
        root = Path("/t/Downloads")
        files = [
            root / "Some Show S01" / "S01E01.mkv",
            root / "Some Movie (2020)" / "Some.Movie.2020.1080p.mkv",
        ]
        self.assertFalse(is_single_tv_pack(files, root))

    def test_infer_season_from_prefixed_ancestor(self) -> None:
        pack = Path("/media/Revolution (2012) S01-S02")
        p = pack / "Revolution (2012) S01" / "Featurettes" / "Gag Reel.mkv"
        self.assertEqual(infer_season_from_path_ancestors(p, pack), 1)


class TestAvatarPackEndToEnd(unittest.TestCase):
    """End-to-end regression: the Avatar inbox layout must bind as a single
    TV pack and the TMDB query must be the show name, not "Book One - Water"."""

    def test_avatar_pack_binds_and_query_is_show_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Avatar - The Last Airbender (2005 - 2008) [1080p]"
            f1 = show / "Book One - Water" / "Avatar - The Last Airbender - S01E01 - The Boy in the Iceberg.mkv"
            f2 = show / "Book Three - Fire" / "Avatar - The Last Airbender - S03E01 - The Awakening.mkv"
            for f in (f1, f2):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 246, "name": "Avatar: The Last Airbender", "first_air_date": "2005-02-21"}
            ]
            tmdb.tv_detail.return_value = {
                "name": "Avatar: The Last Airbender",
                "first_air_date": "2005-02-21",
            }
            tmdb.tv_season.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "The Boy in the Iceberg"},
                ]
            }

            ctx = PlanContext(all_files=[f1, f2], input_root=input_root)
            prepare_pack_tv_resolve(ctx, tmdb, input_root)

            # The pack must bind under the show root, not be skipped.
            self.assertIn(show.resolve(), ctx.entity_packs)
            packed = ctx.entity_packs[show.resolve()]
            self.assertEqual(packed.tmdb_tv_id, 246)

            # The TMDB query MUST be the show name — never "Book One - Water"
            # (pre-fix the per-file fallback searched the season folder name).
            queries = [call.args[0] for call in tmdb.search_tv.call_args_list]
            self.assertTrue(queries, "expected at least one search_tv call")
            for q in queries:
                self.assertNotIn("Book One", q, f"Book One leaked into query: {q!r}")
                self.assertNotIn("Book Three", q, f"Book Three leaked into query: {q!r}")
                self.assertNotIn("Water", q, f"Water leaked into query: {q!r}")
                self.assertNotIn("Fire", q, f"Fire leaked into query: {q!r}")
            self.assertIn("Avatar", queries[0])

    def test_avatar_episodes_resolve_under_pack_binding(self) -> None:
        """Files under the bound pack resolve as episodes with the right
        season numbers (Book One → S01, Book Three → S03), without any extra
        TMDB show searches."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Avatar - The Last Airbender (2005 - 2008) [1080p]"
            f1 = show / "Book One - Water" / "Avatar - The Last Airbender - S01E01 - The Boy in the Iceberg.mkv"
            f3 = show / "Book Three - Fire" / "Avatar - The Last Airbender - S03E01 - The Awakening.mkv"
            for f in (f1, f3):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 246, "name": "Avatar: The Last Airbender", "first_air_date": "2005-02-21"}
            ]
            tmdb.tv_detail.return_value = {
                "name": "Avatar: The Last Airbender",
                "first_air_date": "2005-02-21",
            }
            tmdb.tv_season.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "Pilot Ep"},
                ]
            }

            ctx = PlanContext(all_files=[f1, f3], input_root=input_root)
            prepare_pack_tv_resolve(ctx, tmdb, input_root)
            out = Path(td) / "out"
            e1 = resolve_path(f1, out, tmdb, ctx, ignore_tmdb=False)
            e3 = resolve_path(f3, out, tmdb, ctx, ignore_tmdb=False)

            self.assertEqual(e1.kind, "episode")
            self.assertEqual(e3.kind, "episode")
            self.assertEqual(e1.season, 1)
            self.assertEqual(e3.season, 3)
            # Single TMDB show search (the pack-TV bind) — no per-file re-search.
            self.assertEqual(tmdb.search_tv.call_count, 1)


class TestRevolutionPackEndToEnd(unittest.TestCase):
    """End-to-end regression for the Revolution inbox layout: the pack must
    bind to one TMDB show and specials-arc subfolders (`Wheatley's Letters/`)
    must resolve as S00 specials of that show, not per-file TMDB searches."""

    def test_revolution_pack_binds_and_specials_arc_resolves_as_specials(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Revolution (2012) S01-S02 (1080p BluRay x265 10bit EAC3 5.1 Celdra)"
            s01 = (
                show
                / "Revolution (2012) S01"
                / "Revolution (2012) - S01E01 - Pilot (1080p BluRay x265 Celdra).mkv"
            )
            s01_extra = (
                show
                / "Revolution (2012) S01"
                / "Featurettes"
                / "Season 1 - Gag Reel.mkv"
            )
            s02 = (
                show
                / "Revolution (2012) S02"
                / "Revolution (2012) - S02E01 - Born in the U.S.A. (1080p BluRay x265 Celdra).mkv"
            )
            wheatley = (
                show
                / "Revolution (2012) Wheatley's Letters"
                / "Revolution (2012) - S00E11 - Wheatley's Letters May 7th (480p WEB x265 Celdra).mkv"
            )
            enemies = (
                show
                / "Revolution (2012) Enemies of the State"
                / "Revolution (2012) - S00E17 - Enemies of the State Part 1 (1080p BluRay x265 Celdra).mkv"
            )
            all_files = [s01, s01_extra, s02, wheatley, enemies]
            for f in all_files:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 1410, "name": "Revolution", "first_air_date": "2012-09-17"}
            ]
            tmdb.tv_detail.return_value = {"name": "Revolution", "first_air_date": "2012-09-17"}
            tmdb.tv_season.return_value = {"episodes": []}

            ctx = PlanContext(all_files=all_files, input_root=input_root)
            prepare_pack_tv_resolve(ctx, tmdb, input_root)

            self.assertIn(show.resolve(), ctx.entity_packs, "Revolution pack must bind")
            self.assertEqual(ctx.entity_packs[show.resolve()].tmdb_tv_id, 1410)

            # Show search query must be the show name — not "Revolution Wheatley's Letters"
            # nor "Revolution Enemies of the State" (pre-fix per-file resolve leaked the
            # specials-arc subfolder name into the TMDB query).
            queries = [call.args[0] for call in tmdb.search_tv.call_args_list]
            for q in queries:
                self.assertNotIn("Wheatley", q, f"specials-arc name leaked into query: {q!r}")
                self.assertNotIn("Enemies", q, f"specials-arc name leaked into query: {q!r}")

            out = Path(td) / "out"
            e_s01 = resolve_path(s01, out, tmdb, ctx, ignore_tmdb=False)
            e_s02 = resolve_path(s02, out, tmdb, ctx, ignore_tmdb=False)
            e_wheatley = resolve_path(wheatley, out, tmdb, ctx, ignore_tmdb=False)
            e_enemies = resolve_path(enemies, out, tmdb, ctx, ignore_tmdb=False)

            self.assertEqual(e_s01.kind, "episode")
            self.assertEqual(e_s01.season, 1)
            self.assertEqual(e_s02.kind, "episode")
            self.assertEqual(e_s02.season, 2)
            # Specials arc → season 0 via SxxEyy on the filename, not per-file TMDB search.
            self.assertEqual(e_wheatley.kind, "episode")
            self.assertEqual(e_wheatley.season, 0)
            self.assertEqual(e_wheatley.episode, 11)
            self.assertEqual(e_enemies.kind, "episode")
            self.assertEqual(e_enemies.season, 0)
            self.assertEqual(e_enemies.episode, 17)
            # Specials render under Specials/, not Season 00/.
            self.assertIn("Specials", str(e_wheatley.dest))
            self.assertIn("Specials", str(e_enemies.dest))
            # Featurettes under a prefixed season folder infer season from ancestor.
            e_s01_extra = resolve_path(s01_extra, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(e_s01_extra.kind, "extra")
            self.assertEqual(e_s01_extra.season, 1)


class TestInferSeason(unittest.TestCase):
    def test_season_from_featurettes_path(self) -> None:
        pack = Path("/media/Mad Men (2007)")
        p = pack / "Featurettes" / "Season 3" / "Foo.mkv"
        self.assertEqual(infer_season_from_path_ancestors(p, pack), 3)

    def test_nearest_season_wins(self) -> None:
        pack = Path("/media/show")
        p = pack / "Featurettes" / "Season 2" / "Sub" / "Clip.mkv"
        self.assertEqual(infer_season_from_path_ancestors(p, pack), 2)


class TestInferPlexExtraFolder(unittest.TestCase):
    def test_featurettes_ancestor(self) -> None:
        ent = Path("/in/Mad Men")
        p = ent / "Featurettes" / "Season 2" / "Bonus.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Featurettes")

    def test_flat_season_file_is_other(self) -> None:
        ent = Path("/in/Mad Men")
        p = ent / "Season 07" / "Gay Rights.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Other")

    def test_nearest_extra_folder_wins(self) -> None:
        ent = Path("/in/Show")
        p = ent / "Trailers" / "Featurettes" / "Season 1" / "x.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Featurettes")

    def test_inline_suffix_trailer(self) -> None:
        ent = Path("/in/Show")
        p = ent / "Season 1" / "Teaser Trailer-trailer.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Trailers")

    def test_inline_suffix_behindthescenes(self) -> None:
        ent = Path("/in/Show")
        p = ent / "Season 1" / "Making Of-behindthescenes.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Behind The Scenes")

    def test_clips_maps_to_other(self) -> None:
        ent = Path("/in/Show")
        p = ent / "Season 1" / "Clips" / "a.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Other")

    def test_theme_music_maps_to_other(self) -> None:
        ent = Path("/in/Show")
        p = ent / "Season 1" / "theme-music" / "t.mkv"
        self.assertEqual(infer_plex_extra_folder(p, entity_root=ent), "Other")


class TestBuildSeasonExtraDest(unittest.TestCase):
    def test_extra_under_season_folder(self) -> None:
        out = Path("/lib")
        src = Path("/in/Featurettes/Season 2/An Era of Style.mkv")
        cat = infer_plex_extra_folder(src, entity_root=Path("/in"))
        d = build_season_extra_dest(
            out,
            "Mad Men",
            2,
            src,
            tmdb_tv_id=1100,
            display_title="An Era of Style",
            plex_extra_folder=cat,
        )
        self.assertIn("Series", d.parts)
        self.assertIn("Season 02", d.parts)
        self.assertIn("Featurettes", d.parts)
        self.assertTrue(d.name.endswith(".mkv"))
        self.assertIn("An Era of Style", d.name)

    def test_default_category_is_other(self) -> None:
        out = Path("/lib")
        src = Path("/in") / "Season 2" / "orphan.mkv"
        d = build_season_extra_dest(out, "Show", 2, src, tmdb_tv_id=1)
        self.assertIn("Other", d.parts)


class TestPreparePackResolve(unittest.TestCase):
    def test_prepare_sets_pack_when_tmdb_returns_hit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Show Name (2010)"
            f1 = show / "Season 1" / "Show Name (2010) - S01E01 - Pilot.mkv"
            f1.parent.mkdir(parents=True, exist_ok=True)
            f1.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 42, "name": "Show Name", "first_air_date": "2010-01-01", "overview": "x"},
            ]
            tmdb.tv_detail.return_value = {"name": "Show Name", "original_name": "Show Name"}

            ctx = PlanContext(all_files=[f1])
            prepare_pack_tv_resolve(ctx, tmdb, input_root)

            packed = ctx.entity_packs[show.resolve()]
            self.assertEqual(packed.tmdb_tv_id, 42)
            self.assertEqual(packed.series_name, "Show Name")
            self.assertIn(show.resolve(), ctx.series_by_root)

    def test_resolve_path_extra_under_pack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Show (2011)"
            ep = show / "Season 1" / "S01E01.Pilot.mkv"
            ex = show / "Featurettes" / "Season 2" / "Bonus.mkv"
            ep.parent.mkdir(parents=True, exist_ok=True)
            ex.parent.mkdir(parents=True, exist_ok=True)
            ep.write_bytes(b"")
            ex.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 99, "name": "Show", "first_air_date": "2011-06-01", "overview": "y"},
            ]
            tmdb.tv_detail.return_value = {"name": "Show", "original_name": "Show"}
            tmdb.tv_season.return_value = {
                "episodes": [
                    {"episode_number": 1, "name": "Pilot"},
                ],
            }

            ctx = PlanContext(all_files=[ep, ex], input_root=input_root)
            prepare_pack_tv_resolve(ctx, tmdb, input_root)

            out = Path(td) / "out"
            ent_ex = resolve_path(ex, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(ent_ex.kind, "extra")
            self.assertEqual(ent_ex.season, 2)
            self.assertIsNotNone(ent_ex.dest)
            self.assertIn("Season 02", str(ent_ex.dest))
            self.assertIn("Featurettes", ent_ex.dest.parts)

            ent_ep = resolve_path(ep, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(ent_ep.kind, "episode")
            self.assertIsNotNone(ent_ep.dest)

    def test_pack_featurette_with_no_season_ancestor_defaults_to_specials(self) -> None:
        """Firefly's Featurettes/Adam Baldwin Sings...mkv (no Season ancestor) → Specials."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Firefly (2002) Season 1 S01"
            ep = show / "Firefly (2002) - S01E01 - Serenity.mkv"
            extra = show / "Featurettes" / "Adam Baldwin Sings the Hero of Canton Theme.mkv"
            ep.parent.mkdir(parents=True, exist_ok=True)
            extra.parent.mkdir(parents=True, exist_ok=True)
            ep.write_bytes(b"")
            extra.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 1437, "name": "Firefly", "first_air_date": "2002-09-20", "overview": ""},
            ]
            tmdb.tv_detail.return_value = {"name": "Firefly", "original_name": "Firefly"}

            ctx = PlanContext(all_files=[ep, extra], input_root=input_root)
            prepare_pack_tv_resolve(ctx, tmdb, input_root)
            self.assertIn(show.resolve(), ctx.entity_packs)

            out = Path(td) / "out"
            entry = resolve_path(extra, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(entry.kind, "extra")
            self.assertEqual(entry.season, 0)
            self.assertIsNotNone(entry.dest)
            self.assertIn("Specials", entry.dest.parts)
            self.assertIn("Featurettes", entry.dest.parts)

    def test_extras_with_sxxeyy_in_filename_route_as_season_extras(self) -> None:
        """Firefly/Featurettes/Deleted Scenes/S01E01 Serenity - Scene 1.mkv:
        SxxEyy in the filename must NOT route to resolve_episode (which would
        re-search TMDB for the parent folder name "Deleted Scenes"). Under an
        extras container, the file is a season extra: season comes from SxxEyy."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            show = input_root / "Firefly (2002) Season 1 S01"
            ep = show / "Firefly (2002) - S01E01 - Serenity.mkv"
            scene = show / "Featurettes" / "Deleted Scenes" / "S01E01 Serenity - Scene 1.mkv"
            ep.parent.mkdir(parents=True, exist_ok=True)
            scene.parent.mkdir(parents=True, exist_ok=True)
            ep.write_bytes(b"")
            scene.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_tv.return_value = [
                {"id": 1437, "name": "Firefly", "first_air_date": "2002-09-20", "overview": ""},
            ]
            tmdb.tv_detail.return_value = {"name": "Firefly", "original_name": "Firefly"}

            ctx = PlanContext(all_files=[ep, scene], input_root=input_root)
            prepare_pack_tv_resolve(ctx, tmdb, input_root)

            out = Path(td) / "out"
            entry = resolve_path(scene, out, tmdb, ctx, ignore_tmdb=False)
            # Must be a season extra (no re-search for "Deleted Scenes")
            self.assertEqual(entry.kind, "extra")
            self.assertEqual(entry.season, 1)
            self.assertIsNotNone(entry.dest)
            self.assertIn("Season 01", entry.dest.parts)
            self.assertIn("Deleted Scenes", entry.dest.parts)
            # And critically: the binder did NOT re-search TMDB after the
            # initial pack-TV pick (which already returned in prepare_pack_tv_resolve).
            self.assertEqual(tmdb.search_tv.call_count, 1)
