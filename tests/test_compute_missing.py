"""_compute_missing: detect TMDB-expected episodes absent from the pack.

Uses a MagicMock TmdbClient — we never hit the network. Verifies the
single-season formatting, multi-season `SxxEyy` formatting, and the
"single-episode single-season pack" exemption.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from titleforge.resolve import PlanContext, _compute_missing


def _season_payload(numbers: list[int]) -> dict:
    return {"episodes": [{"episode_number": n} for n in numbers]}


class TestComputeMissing(unittest.TestCase):
    def _ctx(self) -> PlanContext:
        return PlanContext(all_files=[])

    def test_single_season_missing_one(self) -> None:
        ctx = self._ctx()
        tmdb = MagicMock()
        tmdb.tv_season.return_value = _season_payload(list(range(1, 14)))  # E1..E13
        present = set(range(1, 13))  # missing E13
        out = _compute_missing({1: present}, ctx, tmdb, tv_id=999)
        self.assertEqual(out, "E13")

    def test_single_season_full_pack_returns_empty(self) -> None:
        ctx = self._ctx()
        tmdb = MagicMock()
        tmdb.tv_season.return_value = _season_payload(list(range(1, 14)))
        out = _compute_missing({1: set(range(1, 14))}, ctx, tmdb, tv_id=1)
        self.assertEqual(out, "")

    def test_single_episode_single_season_is_exempt(self) -> None:
        """User likely captured one episode on purpose — don't shout."""
        ctx = self._ctx()
        tmdb = MagicMock()
        tmdb.tv_season.return_value = _season_payload(list(range(1, 14)))
        out = _compute_missing({3: {5}}, ctx, tmdb, tv_id=1)
        self.assertEqual(out, "")

    def test_multi_season_uses_sxe_format(self) -> None:
        ctx = self._ctx()
        tmdb = MagicMock()

        def fake_season(tv_id: int, season: int) -> dict:
            # Same 13 episodes per season for the test.
            return _season_payload(list(range(1, 14)))

        tmdb.tv_season.side_effect = fake_season
        by_season = {
            1: set(range(1, 14)) - {7},        # missing E7
            3: set(range(1, 14)) - {2, 3, 4, 5},  # missing E2-E5
        }
        out = _compute_missing(by_season, ctx, tmdb, tv_id=1)
        self.assertEqual(out, "S01E7, S03E2-S03E5")

    def test_season_zero_skipped(self) -> None:
        """Specials inventory on TMDB is too noisy to trust."""
        ctx = self._ctx()
        tmdb = MagicMock()
        tmdb.tv_season.return_value = _season_payload(list(range(1, 20)))
        # Multi-season so the single-episode exemption doesn't kick in.
        by_season = {0: {1}, 1: set(range(1, 14))}
        out = _compute_missing(by_season, ctx, tmdb, tv_id=1)
        # Season 0 is skipped; season 1 is complete → no missing.
        self.assertEqual(out, "")

    def test_season_cache_avoids_double_fetch(self) -> None:
        ctx = self._ctx()
        tmdb = MagicMock()
        tmdb.tv_season.return_value = _season_payload(list(range(1, 14)))
        _compute_missing({1: set(range(1, 13))}, ctx, tmdb, tv_id=42)
        # Re-invoke for the same (tv_id, season): payload comes from ctx.season_cache.
        _compute_missing({1: set(range(1, 12))}, ctx, tmdb, tv_id=42)
        tmdb.tv_season.assert_called_once_with(42, 1)


if __name__ == "__main__":
    unittest.main()
