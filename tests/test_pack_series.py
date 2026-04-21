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


class TestBuildSeasonExtraDest(unittest.TestCase):
    def test_extra_under_season_folder(self) -> None:
        out = Path("/lib")
        src = Path("/in/Featurettes/Season 2/An Era of Style.mkv")
        d = build_season_extra_dest(
            out,
            "Mad Men",
            2,
            src,
            tmdb_tv_id=1100,
            display_title="An Era of Style",
        )
        self.assertIn("Series", d.parts)
        self.assertIn("Season 02", d.parts)
        self.assertTrue(d.name.endswith(".mkv"))
        self.assertIn("An Era of Style", d.name)


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

            self.assertEqual(ctx.entity_packs[show.resolve()], (42, "Show Name"))
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

            ent_ep = resolve_path(ep, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(ent_ep.kind, "episode")
            self.assertIsNotNone(ent_ep.dest)
