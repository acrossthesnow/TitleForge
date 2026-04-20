"""
Conservative title + year extraction from file stems for TMDB search (no extension).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from titleforge.normalize import strip_release_info

# Title (1937) at end of stem
_PAREN_YEAR = re.compile(r"(?i)^(.+?)\s*\(((?:19|20)\d{2})\)\s*$")
# 1937 - Title or Title - 1937 (full stem, hyphen-separated year only at edges)
_YEAR_PREFIX = re.compile(r"^(?P<y>(?:19|20)\d{2})\s*-\s*(?P<t>.+)$")
_YEAR_SUFFIX = re.compile(r"^(?P<t>.+?)\s*-\s*(?P<y>(?:19|20)\d{2})\s*$")


@dataclass(frozen=True)
class CleanedQuery:
    """Title text for TMDB `query` and optional year for movie/year or TV first_air_date_year."""

    title: str
    year: int | None
    raw_stem: str
    stripped_year_note: str | None  # for UI


def clean_stem_for_search(stem: str) -> CleanedQuery:
    """
    Pass ``path.stem`` only (extension never included).
    Years only from (YYYY) at end, or ``YYYY - title`` / ``title - YYYY`` on the full stem.
    """
    raw = stem.strip()
    year: int | None = None
    note: str | None = None
    t = raw

    if m := _PAREN_YEAR.match(raw):
        t = m.group(1).strip()
        year = int(m.group(2))
        note = "year from (…)"
    elif m := _YEAR_PREFIX.match(raw):
        t = m.group("t").strip()
        year = int(m.group("y"))
        note = "year before title"
    elif m := _YEAR_SUFFIX.match(raw):
        t = m.group("t").strip()
        year = int(m.group("y"))
        note = "year after title"

    t = strip_release_info(t, aggressive=True)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        t = strip_release_info(raw, aggressive=False)
        t = re.sub(r"\s+", " ", t).strip()

    return CleanedQuery(title=t, year=year, raw_stem=raw, stripped_year_note=note)
