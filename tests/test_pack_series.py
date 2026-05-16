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
)
from titleforge.extra_category import infer_plex_extra_folder
from titleforge.plex_paths import build_season_extra_dest
from titleforge.resolve import PlanContext, prepare_pack_tv_resolve, resolve_path
from titleforge.series_folder import series_group_root


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
