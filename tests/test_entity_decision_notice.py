"""_entity_decision_notice: terse, colored on TTY, plain when piped."""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from titleforge.resolve import _entity_decision_notice


def _capture(*, isatty: bool, **kwargs) -> str:
    buf = io.StringIO()
    # io.StringIO has no `.isatty()` returning True; patch it.
    buf.isatty = lambda: isatty  # type: ignore[method-assign]
    with patch.object(sys, "stderr", buf):
        _entity_decision_notice(**kwargs)
    return buf.getvalue()


class TestEntityDecisionNotice(unittest.TestCase):
    def test_no_source_folder_prefix(self) -> None:
        entity = Path("/inbox/Firefly (2002) Season 1 S01 (1080p BluRay x265 HEVC 10bit AAC Silence)")
        out = _capture(
            isatty=False,
            kind="TV",
            title="Firefly",
            year=2002,
            tmdb_id=1437,
            entity=entity,
        )
        # The noisy source folder name MUST NOT appear in a confident decision line.
        self.assertNotIn("Season 1 S01", out)
        self.assertNotIn("BluRay", out)
        self.assertNotIn("Silence", out)
        # The picked title + id are present.
        self.assertIn("Firefly", out)
        self.assertIn("(2002)", out)
        self.assertIn("{tmdb-1437}", out)

    def test_plain_output_when_not_tty(self) -> None:
        out = _capture(
            isatty=False,
            kind="MOVIE",
            title="The Martian",
            year=2015,
            tmdb_id=286217,
            entity=Path("/inbox/The.Martian.2015.mkv"),
        )
        # No ANSI escape sequences when stderr is piped/logged.
        self.assertNotIn("\033[", out)
        self.assertEqual(out.strip(), "[MOVIE] The Martian (2015) {tmdb-286217}")

    def test_colored_output_when_tty(self) -> None:
        out = _capture(
            isatty=True,
            kind="MOVIE",
            title="The Martian",
            year=2015,
            tmdb_id=286217,
            entity=Path("/inbox/The.Martian.2015.mkv"),
        )
        # Some ANSI escapes are present for the kind tag and the id tag.
        self.assertIn("\033[", out)
        # Reset comes through at least twice (kind tag + id tag).
        self.assertGreaterEqual(out.count("\033[0m"), 2)

    def test_tv_and_movie_use_distinct_colors_on_tty(self) -> None:
        movie = _capture(
            isatty=True,
            kind="MOVIE",
            title="X",
            year=None,
            tmdb_id=1,
            entity=Path("/x"),
        )
        tv = _capture(
            isatty=True,
            kind="TV",
            title="X",
            year=None,
            tmdb_id=1,
            entity=Path("/x"),
        )
        self.assertNotEqual(movie, tv)


if __name__ == "__main__":
    unittest.main()
