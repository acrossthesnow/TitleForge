"""Tests for stem parsing: title + year for TMDB search."""

from __future__ import annotations

import unittest

from titleforge.query_clean import clean_stem_for_search


class TestParenYear(unittest.TestCase):
    def test_atlantis_paren_year_stripped_from_title(self) -> None:
        stem = "Atlantis - The Lost Empire (2001)"
        c = clean_stem_for_search(stem)
        self.assertEqual(c.year, 2001)
        self.assertEqual(c.title, "Atlantis - The Lost Empire")
        self.assertNotIn("2001)", c.title)
        self.assertEqual(c.raw_stem, stem)

    def test_year_prefix_hyphen(self) -> None:
        c = clean_stem_for_search("2001 - A Space Odyssey")
        self.assertEqual(c.year, 2001)
        self.assertEqual(c.title, "A Space Odyssey")

    def test_year_suffix_hyphen(self) -> None:
        c = clean_stem_for_search("Some Show - 2020")
        self.assertEqual(c.year, 2020)
        self.assertEqual(c.title, "Some Show")


if __name__ == "__main__":
    unittest.main()
