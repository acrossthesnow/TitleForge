from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

KindGuess = Literal["episode", "movie", "ambiguous"]

# Strong episode patterns (FileBot-style S00E00 / 1x02)
_S00E00 = re.compile(r"(?i)\bS(\d{1,4})[\s._-]*E(\d{1,4})\b")
_NxNN = re.compile(r"(?i)\b(\d{1,2})x(\d{2,3})\b")
_SE_WORDS = re.compile(
    r"(?i)\bSeason\s*(\d{1,4})\s*(?:Episode|Ep\.?)\s*(\d{1,4})\b",
)
_EP_PREFIX = re.compile(r"(?i)\bEp\.?\s*(\d{1,4})\b")

# Movie year at end of stem: Title (2009), optionally followed by [tag] groups.
_NAME_YEAR = re.compile(r"(.+?)\s*\(((?:19|20)\d{2})\)\s*(?:\[[^\]]*\]\s*)*$")
# Scene-style: Title.YYYY.release-tail. Requires a release token in the tail so
# `Show 2020 S01E01` doesn't get classified as a movie (looks_episode wins anyway
# because parse_sxe matches first, but defending against false positives here).
_NAME_DOT_YEAR = re.compile(
    r"^(?P<t>.+?)[.\s_-]+(?P<y>(?:19|20)\d{2})[.\s_-]+(?P<rest>.+)$"
)
_RELEASE_TAIL = re.compile(
    r"(?i)\b(720p|1080p|2160p|4k|web-?dl|webrip|bluray|bdrip|brrip|dvdrip|hdtv|"
    r"remux|extended|unrated|repack|proper|multi|x264|x265|hevc|h\.?264|h\.?265|"
    r"av1|hdr\d*|sdr|uhd|dv|truehd|atmos|dts|aac\d*|ac3|eac3|ddp?\d|imax|"
    r"amzn|nf|hmax|dsnp|hulu|atvp|pcok|stan|crave|starz)\b"
)

# Optional dated episode YYYY-MM-DD (simple)
_DATE_EP = re.compile(r"(?i)\b(19|20)\d{2}[.\-_](0[1-9]|1[0-2])[.\-_](0[1-9]|[12]\d|3[01])\b")


def parse_sxe(path: Path) -> tuple[int, int] | None:
    """Return (season, episode) for standard episodes; None if not matched."""
    text = f"{path.parent.name}/{path.name}"
    m = _S00E00.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _NxNN.search(path.name)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _SE_WORDS.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def looks_episode(path: Path) -> bool:
    if parse_sxe(path) is not None:
        return True
    if _DATE_EP.search(path.name):
        return True
    if _EP_PREFIX.search(path.name) and not _NAME_YEAR.match(path.stem):
        return True
    return False


def looks_movie(path: Path) -> bool:
    if looks_episode(path):
        return False
    stem = path.stem.strip()
    if _NAME_YEAR.match(stem):
        return True
    m = _NAME_DOT_YEAR.match(stem)
    if m and _RELEASE_TAIL.search(m.group("rest")):
        return True
    return False


def guess_kind(path: Path) -> KindGuess:
    """Episode signals win over movie (FileBot MediaDetection precedence)."""
    if looks_episode(path):
        return "episode"
    if looks_movie(path):
        return "movie"
    # Short names, documentaries, etc.
    if len(path.stem) < 4:
        return "ambiguous"
    return "ambiguous"


def series_query_string(path: Path) -> str:
    """Derive a TMDB TV search string from folder / filename."""
    from titleforge.normalize import strip_release_info

    parent = strip_release_info(path.parent.name, aggressive=True)
    # Remove S01 / Season 1 from folder name
    parent = re.sub(r"(?i)\bS\d{1,4}\b", " ", parent)
    parent = re.sub(r"(?i)\bSeason\s*\d{1,4}\b", " ", parent)
    parent = re.sub(r"\s+", " ", parent).strip()
    if parent:
        return parent
    stem = strip_release_info(path.stem, aggressive=True)
    stem = _S00E00.sub(" ", stem)
    stem = _NxNN.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem or path.stem
