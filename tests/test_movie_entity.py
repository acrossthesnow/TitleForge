"""Movie-folder pre-binding: one TMDB pick per folder, applied to all member files."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.resolve import (
    MovieEntityBinding,
    PlanContext,
    prepare_movie_entity_resolve,
    resolve_path,
)


class TestPrepareMovieEntityResolve(unittest.TestCase):
    def test_paren_year_folder_binds_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ent = input_root / "Final Fantasy The Spirits Within (2001) [1080p]"
            f = ent / "Final.Fantasy.The.Spirits.Within.2001.1080p.BrRip.x264.BOKIUTOX.YIFY.mp4"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_movie.return_value = [
                {
                    "id": 2114,
                    "title": "Final Fantasy: The Spirits Within",
                    "release_date": "2001-07-11",
                    "overview": "",
                },
            ]
            tmdb.movie_detail.return_value = {
                "id": 2114,
                "title": "Final Fantasy: The Spirits Within",
                "release_date": "2001-07-11",
            }

            ctx = PlanContext(all_files=[f], input_root=input_root)
            prepare_movie_entity_resolve(ctx, tmdb, input_root)

            self.assertIn(ent.resolve(), ctx.entity_movies)
            bind = ctx.entity_movies[ent.resolve()]
            self.assertEqual(bind.tmdb_movie_id, 2114)
            self.assertEqual(bind.year, 2001)
            self.assertIn("Final Fantasy", bind.title)

            # Resolve the inside file via the binding.
            out = Path(td) / "out"
            entry = resolve_path(f, out, tmdb, ctx, ignore_tmdb=False)
            self.assertEqual(entry.kind, "movie")
            self.assertEqual(entry.tmdb_movie_id, 2114)
            self.assertIsNotNone(entry.dest)
            self.assertIn("Movies", entry.dest.parts)
            self.assertIn("{tmdb-2114}", str(entry.dest))

    def test_dot_year_folder_binds(self) -> None:
        """The Martian-style folder name (dot-year, no parens)."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ent = input_root / "The.Martian.2015.EXTENDED.2160p.BluRay.x265-TERMiNAL"
            f = ent / "The.Martian.2015.EXTENDED.2160p.UHD.BluRay.x265-TERMiNAL.mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            tmdb.search_movie.return_value = [
                {"id": 286217, "title": "The Martian", "release_date": "2015-09-30", "overview": ""},
            ]
            tmdb.movie_detail.return_value = {
                "id": 286217,
                "title": "The Martian",
                "release_date": "2015-09-30",
            }

            ctx = PlanContext(all_files=[f], input_root=input_root)
            prepare_movie_entity_resolve(ctx, tmdb, input_root)

            self.assertIn(ent.resolve(), ctx.entity_movies)
            self.assertEqual(ctx.entity_movies[ent.resolve()].tmdb_movie_id, 286217)

    def test_jurassic_park_collection_does_not_bind(self) -> None:
        """`Jurassic Park COLLECTION 1993-2015 ...` is a collection — defer to per-file."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ent = input_root / "Jurassic Park COLLECTION 1993-2015 2160p BluRay REMUX HEVC.DTS-HD.MA.7.1-LEGi0N [RiCK]"
            f = ent / "Jurassic.Park.1993.REMUX.2160p.BluRay.mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            ctx = PlanContext(all_files=[f], input_root=input_root)
            prepare_movie_entity_resolve(ctx, tmdb, input_root)
            self.assertNotIn(ent.resolve(), ctx.entity_movies)
            tmdb.search_movie.assert_not_called()

    def test_collection_hint_alone_defers(self) -> None:
        """`Marvel Cinematic Universe Collection` has no years but COLLECTION hint."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ent = input_root / "Marvel Cinematic Universe Collection"
            f = ent / "Iron.Man.2008.1080p.BluRay.mkv"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"")

            tmdb = MagicMock()
            ctx = PlanContext(all_files=[f], input_root=input_root)
            prepare_movie_entity_resolve(ctx, tmdb, input_root)
            self.assertNotIn(ent.resolve(), ctx.entity_movies)
            tmdb.search_movie.assert_not_called()

    def test_movie_entity_extras_under_featurettes(self) -> None:
        """Movie-bound entity with a Featurettes/ subfolder → Plex local-extras layout."""
        with tempfile.TemporaryDirectory() as td:
            input_root = Path(td)
            ent = input_root / "Some Movie (2020)"
            main = ent / "Some.Movie.2020.1080p.BluRay.x264.mkv"
            extra = ent / "Featurettes" / "Behind The Scenes.mkv"
            main.parent.mkdir(parents=True, exist_ok=True)
            extra.parent.mkdir(parents=True, exist_ok=True)
            main.write_bytes(b"")
            extra.write_bytes(b"")

            ctx = PlanContext(
                all_files=[main, extra],
                input_root=input_root,
                entity_movies={
                    ent.resolve(): MovieEntityBinding(
                        tmdb_movie_id=999, title="Some Movie", year=2020
                    ),
                },
            )

            out = Path(td) / "out"
            entry_main = resolve_path(main, out, MagicMock(), ctx, ignore_tmdb=False)
            entry_extra = resolve_path(extra, out, MagicMock(), ctx, ignore_tmdb=False)

            self.assertEqual(entry_main.kind, "movie")
            self.assertEqual(entry_main.tmdb_movie_id, 999)
            self.assertIn("Some Movie (2020) {tmdb-999}", str(entry_main.dest))

            self.assertEqual(entry_extra.kind, "extra")
            self.assertEqual(entry_extra.tmdb_movie_id, 999)
            self.assertIn("Featurettes", entry_extra.dest.parts)
            self.assertIn("Some Movie (2020) {tmdb-999}", str(entry_extra.dest))


if __name__ == "__main__":
    unittest.main()
