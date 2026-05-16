"""rescue_orphan_sidecars end-to-end: find orphan .srt, look up TMDB,
move next to the destination movie video, rename to match."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from titleforge.rescue import rescue_orphan_sidecars


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


class TestRescueOrphanSidecars(unittest.TestCase):
    def test_moves_orphan_srt_to_matching_dest_video(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "inbox"
            out = root / "lib"
            # Source folder: original movie videos are gone; only sidecars + junk remain.
            source = inp / "Final Fantasy The Spirits Within (2001) [1080p]"
            orphan = source / "Final.Fantasy.The.Spirits.Within.2001.1080p.BrRip.x264.BOKIUTOX.YIFY.srt"
            _touch(orphan)
            _touch(source / "RARBG.txt")
            # Destination video already in place.
            dest_folder = out / "Movies" / "Final Fantasy The Spirits Within (2001) {tmdb-2114}"
            dest_video = dest_folder / "Final Fantasy The Spirits Within (2001).mp4"
            _touch(dest_video)

            tmdb = MagicMock()
            tmdb.search_movie.return_value = [
                {
                    "id": 2114,
                    "title": "Final Fantasy: The Spirits Within",
                    "release_date": "2001-07-11",
                }
            ]

            result = rescue_orphan_sidecars(inp, out, tmdb)

            self.assertEqual(len(result.moved), 1)
            src, new = result.moved[0]
            self.assertEqual(src.resolve(), orphan.resolve())
            self.assertEqual(
                new.resolve(),
                (dest_folder / "Final Fantasy The Spirits Within (2001).srt").resolve(),
            )
            # The orphan is gone, the new path exists.
            self.assertFalse(orphan.exists())
            self.assertTrue(new.exists())

    def test_does_not_move_when_video_sibling_still_present(self) -> None:
        """A sidecar next to its original video is NOT orphan."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "inbox"
            out = root / "lib"
            source = inp / "Movie (2020)"
            video = source / "Movie.2020.mkv"
            srt = source / "Movie.2020.srt"
            _touch(video)
            _touch(srt)

            tmdb = MagicMock()
            result = rescue_orphan_sidecars(inp, out, tmdb)

            self.assertEqual(result.moved, [])
            self.assertTrue(srt.exists())
            tmdb.search_movie.assert_not_called()

    def test_skips_when_no_confident_tmdb_match(self) -> None:
        """Two TMDB hits with different years — refuse to guess."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "inbox"
            out = root / "lib"
            source = inp / "Generic.Movie.2010.1080p"
            orphan = source / "Generic.Movie.2010.1080p.srt"
            _touch(orphan)

            tmdb = MagicMock()
            # Two candidates, neither year-matches.
            tmdb.search_movie.return_value = [
                {"id": 1, "title": "A", "release_date": "2001-01-01"},
                {"id": 2, "title": "B", "release_date": "2003-01-01"},
            ]
            result = rescue_orphan_sidecars(inp, out, tmdb)
            self.assertEqual(result.moved, [])
            self.assertEqual(len(result.unmatched), 1)
            # Orphan still in place — we don't risk attaching to the wrong movie.
            self.assertTrue(orphan.exists())

    def test_skips_when_dest_folder_missing(self) -> None:
        """TMDB resolves cleanly but the {tmdb-id} folder isn't in --output."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "inbox"
            out = root / "lib"
            source = inp / "Movie (2020)"
            orphan = source / "Movie.2020.srt"
            _touch(orphan)

            tmdb = MagicMock()
            tmdb.search_movie.return_value = [
                {"id": 99, "title": "Movie", "release_date": "2020-01-01"}
            ]
            result = rescue_orphan_sidecars(inp, out, tmdb)
            self.assertEqual(result.moved, [])
            self.assertEqual(len(result.unmatched), 1)
            self.assertTrue(orphan.exists())

    def test_preserves_lang_forced_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "inbox"
            out = root / "lib"
            source = inp / "Movie (2020)"
            orphan_default = source / "Movie.2020.srt"
            orphan_lang = source / "Movie.2020.en.srt"
            orphan_forced = source / "Movie.2020.en.forced.srt"
            for o in (orphan_default, orphan_lang, orphan_forced):
                _touch(o)
            dest_folder = out / "Movies" / "Movie (2020) {tmdb-7}"
            dest_video = dest_folder / "Movie (2020).mkv"
            _touch(dest_video)

            tmdb = MagicMock()
            tmdb.search_movie.return_value = [
                {"id": 7, "title": "Movie", "release_date": "2020-01-01"}
            ]
            result = rescue_orphan_sidecars(inp, out, tmdb)
            new_names = {n.name for _, n in result.moved}
            self.assertEqual(
                new_names,
                {
                    "Movie (2020).srt",
                    "Movie (2020).en.srt",
                    "Movie (2020).en.forced.srt",
                },
            )

    def test_does_not_overwrite_existing_dest_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            inp = root / "inbox"
            out = root / "lib"
            source = inp / "Movie (2020)"
            orphan = source / "Movie.2020.srt"
            _touch(orphan)
            dest_folder = out / "Movies" / "Movie (2020) {tmdb-7}"
            dest_video = dest_folder / "Movie (2020).mkv"
            existing = dest_folder / "Movie (2020).srt"
            _touch(dest_video)
            _touch(existing)

            tmdb = MagicMock()
            tmdb.search_movie.return_value = [
                {"id": 7, "title": "Movie", "release_date": "2020-01-01"}
            ]
            result = rescue_orphan_sidecars(inp, out, tmdb)
            self.assertEqual(result.moved, [])
            # Orphan still in source (not overwriting).
            self.assertTrue(orphan.exists())


if __name__ == "__main__":
    unittest.main()
