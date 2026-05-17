"""
Conservative title + year extraction from file stems for TMDB search (no extension).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from titleforge.normalize import strip_release_info

# Title (1937) at end of stem, optionally followed by [tag] groups like [1080p].
_PAREN_YEAR = re.compile(r"(?i)^(.+?)\s*\(((?:19|20)\d{2})\)\s*(?:\[[^\]]*\]\s*)*$")
# 1937 - Title or Title - 1937 (full stem, hyphen-separated year only at edges)
_YEAR_PREFIX = re.compile(r"^(?P<y>(?:19|20)\d{2})\s*-\s*(?P<t>.+)$")
_YEAR_SUFFIX = re.compile(r"^(?P<t>.+?)\s*-\s*(?P<y>(?:19|20)\d{2})\s*$")
# Scene-style: Title.YYYY.release-tail (dots/spaces/underscores/hyphens as separators).
# Only accepted when the tail looks like release info — otherwise the year may be the
# title's own year (e.g. `Show 2020 S01E01.mkv` should NOT lose its show name here).
_DOT_YEAR = re.compile(
    r"^(?P<t>.+?)[.\s_-]+(?P<y>(?:19|20)\d{2})(?:[.\s_-]+(?P<rest>.+))?$"
)
_RELEASE_TAIL = re.compile(
    r"(?i)\b(720p|1080p|2160p|4k|web-?dl|webrip|bluray|bdrip|brrip|dvdrip|hdtv|"
    r"remux|extended|unrated|repack|proper|multi|x264|x265|hevc|h\.?264|h\.?265|"
    r"av1|hdr\d*|sdr|uhd|dv|truehd|atmos|dts|aac\d*|ac3|eac3|ddp?\d|imax|"
    r"amzn|nf|hmax|dsnp|hulu|atvp|pcok|stan|crave|starz)\b"
)
# Any (YYYY) anywhere in the stem — fallback when nothing else matched (e.g. messy
# folder names like `Firefly (2002) Season 1 S01 (1080p BluRay ...)`).
_ANY_PAREN_YEAR = re.compile(r"\(((?:19|20)\d{2})\)")


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
    elif (m := _DOT_YEAR.match(raw)) and _release_tail_ok(m.group("rest")):
        # Scene-style Title.YYYY.release-tokens: keep only the prefix before the year.
        # `rest` holds noise like `1080p.BrRip.x264.YIFY` — discard entirely so it
        # never reaches TMDB.
        t = m.group("t").strip()
        year = int(m.group("y"))
        note = "year mid-stem"

    if year is None:
        if m := _ANY_PAREN_YEAR.search(raw):
            year = int(m.group(1))
            note = "year from (…) anywhere"

    t = strip_release_info(t, aggressive=True)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        t = strip_release_info(raw, aggressive=False)
        t = re.sub(r"\s+", " ", t).strip()

    return CleanedQuery(title=t, year=year, raw_stem=raw, stripped_year_note=note)


def _release_tail_ok(rest: str | None) -> bool:
    """Empty rest, or rest containing a recognized release token (1080p, BluRay, …)."""
    if not rest:
        return True
    return bool(_RELEASE_TAIL.search(rest))
