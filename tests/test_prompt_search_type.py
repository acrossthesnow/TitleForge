"""Tab-cycle order for the no-results search-type toggle."""

from __future__ import annotations

import unittest

from titleforge.prompt_ui import SEARCH_TYPE_CYCLE, next_search_type


class TestSearchTypeCycle(unittest.TestCase):
    def test_cycle_order_is_movie_tv_both(self) -> None:
        self.assertEqual(SEARCH_TYPE_CYCLE, ("movie", "tv", "both"))

    def test_next_search_type_wraps(self) -> None:
        self.assertEqual(next_search_type("movie"), "tv")
        self.assertEqual(next_search_type("tv"), "both")
        self.assertEqual(next_search_type("both"), "movie")


if __name__ == "__main__":
    unittest.main()
