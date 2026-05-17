"""Pack episode-summary helpers: contiguous-run formatter + season grouping."""

from __future__ import annotations

import unittest
from pathlib import Path

from titleforge.resolve import _format_episode_run, _summarise_pack_seasons


class TestFormatEpisodeRun(unittest.TestCase):
    def test_full_contiguous(self) -> None:
        self.assertEqual(_format_episode_run(set(range(1, 14))), "E1-E13")

    def test_single_gap(self) -> None:
        eps = set(range(1, 6)) | set(range(7, 14))  # E6 missing
        self.assertEqual(_format_episode_run(eps), "E1-E5, E7-E13")

    def test_multiple_gaps(self) -> None:
        eps = {1, 2, 4, 5, 8}
        self.assertEqual(_format_episode_run(eps), "E1-E2, E4-E5, E8")

    def test_single_episode(self) -> None:
        self.assertEqual(_format_episode_run({5}), "E5")

    def test_two_adjacent_become_a_range(self) -> None:
        self.assertEqual(_format_episode_run({3, 4}), "E3-E4")

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_format_episode_run(set()), "")


class TestSummarisePackSeasons(unittest.TestCase):
    def _files(self, *names: str) -> list[Path]:
        # Paths are only used by parse_sxe, which reads `path.parent.name` and
        # `path.name`. We can construct synthetic paths under a temporary
        # parent — actual filesystem presence isn't required.
        parent = Path("/inbox/Show.S01.1080p")
        return [parent / n for n in names]

    def test_single_season_contiguous(self) -> None:
        files = self._files(*(f"Show.S01E{i:02d}.mkv" for i in range(1, 14)))
        by_season, summary = _summarise_pack_seasons(files)
        self.assertEqual(set(by_season.keys()), {1})
        self.assertEqual(by_season[1], set(range(1, 14)))
        self.assertEqual(summary, "S01 (E1-E13)")

    def test_single_season_with_gap(self) -> None:
        # Skip episode 6.
        nums = list(range(1, 6)) + list(range(7, 14))
        files = self._files(*(f"Show.S01E{i:02d}.mkv" for i in nums))
        _, summary = _summarise_pack_seasons(files)
        self.assertEqual(summary, "S01 (E1-E5, E7-E13)")

    def test_multi_season_aggregate(self) -> None:
        files = []
        for s in (1, 2, 3, 4):
            files.extend(self._files(*(f"Show.S{s:02d}E{e:02d}.mkv" for e in range(1, 14))))
        by_season, summary = _summarise_pack_seasons(files)
        self.assertEqual(sorted(by_season.keys()), [1, 2, 3, 4])
        self.assertEqual(summary, "S01-S04 (52 eps)")

    def test_extras_only_pack_returns_empty_summary(self) -> None:
        # No SxxEyy → parse_sxe returns None → nothing aggregated.
        files = [
            Path("/inbox/Show/Featurettes/Behind the Scenes.mkv"),
            Path("/inbox/Show/Featurettes/Bloopers.mkv"),
        ]
        by_season, summary = _summarise_pack_seasons(files)
        self.assertEqual(by_season, {})
        self.assertEqual(summary, "")

    def test_specials_season_zero_included_in_summary(self) -> None:
        # season 0 is treated like any other for summary purposes; the
        # missing-detection function is what skips it.
        files = self._files(
            "Show.S00E01.mkv",
            "Show.S00E02.mkv",
            "Show.S00E03.mkv",
        )
        by_season, summary = _summarise_pack_seasons(files)
        self.assertEqual(by_season[0], {1, 2, 3})
        self.assertEqual(summary, "S00 (E1-E3)")


if __name__ == "__main__":
    unittest.main()
