"""remove_empty_source_dirs: remove directories with no real videos remaining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from titleforge.cleanup import remove_empty_source_dirs


def _touch(p: Path, content: bytes = b"") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


class TestRemoveEmptySourceDirs(unittest.TestCase):
    def test_fully_emptied_movie_folder_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ent = root / "The.Martian.2015"
            # Post-move residue: a .nfo, .txt, and an .srt — no videos.
            _touch(ent / "RARBG.txt")
            _touch(ent / "The.Martian.2015.nfo")
            _touch(ent / "The.Martian.2015.srt")

            removed = remove_empty_source_dirs(root)

            self.assertIn(ent.resolve(), removed)
            self.assertFalse(ent.exists())

    def test_sample_only_folder_is_treated_as_empty(self) -> None:
        """A folder with only `Sample/sample.mkv` (junk) should be removed —
        discover_videos filters those out so the folder has zero *real* videos."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ent = root / "The.Martian.2015"
            _touch(ent / "Sample" / "the.martian.2015.sample.mkv")
            _touch(ent / "RARBG.txt")

            removed = remove_empty_source_dirs(root)

            self.assertIn(ent.resolve(), removed)
            self.assertFalse(ent.exists())

    def test_folder_with_remaining_real_video_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ent = root / "Some.Show"
            kept_video = ent / "Some.Show.S02E03.mkv"
            _touch(kept_video)
            _touch(ent / "info.txt")

            removed = remove_empty_source_dirs(root)

            self.assertNotIn(ent.resolve(), removed)
            self.assertTrue(ent.exists())
            self.assertTrue(kept_video.exists())

    def test_input_root_itself_is_never_removed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Empty input root with no subdirectories.
            removed = remove_empty_source_dirs(root)
            self.assertEqual(removed, [])
            self.assertTrue(root.exists())

    def test_partial_empties_inside_kept_parent(self) -> None:
        """A parent with one kept video and one empty subdirectory: keep the
        parent, but remove the inner empty subdir."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ent = root / "Show (2020)"
            _touch(ent / "Season 1" / "Show.S01E01.mkv")
            # Featurettes/ has only a sample, so it's effectively empty.
            _touch(ent / "Featurettes" / "trailer.sample.mkv")

            removed = remove_empty_source_dirs(root)

            self.assertNotIn(ent.resolve(), removed)
            self.assertTrue(ent.exists())
            self.assertIn((ent / "Featurettes").resolve(), removed)
            self.assertFalse((ent / "Featurettes").exists())
            self.assertTrue((ent / "Season 1" / "Show.S01E01.mkv").exists())

    def test_non_directory_input_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "not_a_dir.txt"
            _touch(file_path)
            self.assertEqual(remove_empty_source_dirs(file_path), [])


if __name__ == "__main__":
    unittest.main()
