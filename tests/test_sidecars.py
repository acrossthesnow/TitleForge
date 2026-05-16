"""find_sidecars + sidecar_dest: preserve language/forced tags on subtitles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from titleforge.sidecars import find_sidecars, sidecar_dest


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


class TestFindSidecars(unittest.TestCase):
    def test_finds_plain_and_language_tagged_srt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "Final.Fantasy.2001.1080p.mkv"
            _touch(video)
            _touch(root / "Final.Fantasy.2001.1080p.srt")
            _touch(root / "Final.Fantasy.2001.1080p.en.srt")
            _touch(root / "Final.Fantasy.2001.1080p.en.forced.srt")
            # Unrelated sidecar (different base name) — must not be picked.
            _touch(root / "Other.Movie.srt")
            # Non-sidecar text file — must be ignored.
            _touch(root / "Final.Fantasy.2001.1080p.txt")
            # `.eng` language tag with idx — should be picked.
            _touch(root / "Final.Fantasy.2001.1080p.eng.idx")

            found = {p.name for p in find_sidecars(video)}
            self.assertEqual(
                found,
                {
                    "Final.Fantasy.2001.1080p.srt",
                    "Final.Fantasy.2001.1080p.en.srt",
                    "Final.Fantasy.2001.1080p.en.forced.srt",
                    "Final.Fantasy.2001.1080p.eng.idx",
                },
            )

    def test_ignores_files_in_other_directories(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "show" / "Show.S01E01.mkv"
            _touch(video)
            # Sidecar in a different directory — not picked.
            _touch(root / "Show.S01E01.srt")
            # Sidecar next to the video — picked.
            _touch(root / "show" / "Show.S01E01.srt")

            found = [p for p in find_sidecars(video)]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].parent.resolve(), video.parent.resolve())

    def test_does_not_pick_up_prefixed_but_unrelated_file(self) -> None:
        """`MovieName.mkv` should NOT match `MovieName2.srt` — the name after
        the stem must start with a `.` separator."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            video = root / "MovieName.mkv"
            _touch(video)
            _touch(root / "MovieName2.srt")  # not a sidecar of MovieName
            _touch(root / "MovieName.srt")   # this one is

            found = {p.name for p in find_sidecars(video)}
            self.assertEqual(found, {"MovieName.srt"})


class TestSidecarDest(unittest.TestCase):
    def test_plain_srt_destination(self) -> None:
        video = Path("/in/Final.Fantasy.2001.mkv")
        video_dest = Path("/lib/Movies/Final Fantasy (2001) {tmdb-2114}/Final Fantasy (2001).mkv")
        sc = Path("/in/Final.Fantasy.2001.srt")
        self.assertEqual(
            sidecar_dest(sc, video, video_dest),
            Path("/lib/Movies/Final Fantasy (2001) {tmdb-2114}/Final Fantasy (2001).srt"),
        )

    def test_multi_dot_suffix_preserved(self) -> None:
        video = Path("/in/Final.Fantasy.2001.mkv")
        video_dest = Path("/lib/Movies/Final Fantasy (2001)/Final Fantasy (2001).mkv")
        sc = Path("/in/Final.Fantasy.2001.en.forced.srt")
        self.assertEqual(
            sidecar_dest(sc, video, video_dest).name,
            "Final Fantasy (2001).en.forced.srt",
        )


if __name__ == "__main__":
    unittest.main()
