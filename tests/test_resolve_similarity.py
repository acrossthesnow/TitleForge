"""Contract for movie-path title similarity string (matches resolve_movie logic)."""

from __future__ import annotations

import re
import unittest

from titleforge.query_clean import clean_stem_for_search


def _movie_similarity_query_from_stem(stem: str) -> str:
    """Mirror resolve_movie: primary_name = cleaned.title or path.stem."""
    cleaned = clean_stem_for_search(stem)
    primary_name = cleaned.title or stem
    return re.sub(r"\s+", " ", (cleaned.title or primary_name).lower()).strip()


class TestMovieSimilarityQuery(unittest.TestCase):
    def test_atlantis_matches_cleaned_title_lowercase(self) -> None:
        stem = "Atlantis - The Lost Empire (2001)"
        cleaned = clean_stem_for_search(stem)
        q = _movie_similarity_query_from_stem(stem)
        self.assertEqual(q, cleaned.title.lower())
        self.assertEqual(q, "atlantis - the lost empire")
        self.assertNotIn("2001", q)


if __name__ == "__main__":
    unittest.main()
