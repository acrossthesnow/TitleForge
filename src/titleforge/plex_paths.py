"""
Plex-style path segments aligned with FileBot PlexNamingStandard.path() sanitization.
Top-level TV folder is Series (not TV Shows).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

# FileBot PlexNamingStandard.TITLE_MAX_LENGTH
TITLE_MAX_LENGTH = 150

# FileBot FileUtilities.ILLEGAL_CHARACTERS (simplified: no Java 250-char edge case)
_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")
# Zero-width / bidi / BOM — not removed by str.strip(); TMDB ":" → " - " can leave ZWSP at edges.
_INVISIBLE_PATH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")

# Smart quotes → ASCII (Normalization.java)
_QUOTE_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u0060": "'",
        "\u00b4": "'",
        "\u02bb": "'",
    }
)


def replace_colon(s: str) -> str:
    # replaceColon(s, ".", " - ") for ratio and colon — approximate: ":" -> " - "
    s = re.sub(r"\s*:\s*", " - ", s)
    return s


def replace_path_separators(s: str, replacement: str = " ") -> str:
    return re.sub(r"[\\/]+", replacement, s)


def normalize_quotation_marks(s: str) -> str:
    return s.translate(_QUOTE_MAP)


def trim_trailing_punctuation(s: str) -> str:
    s = s.rstrip()
    s = re.sub(r"[!?.]+$", "", s)
    return s.rstrip()


def validate_file_name(s: str) -> str:
    s = _ILLEGAL.sub("", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    # trailing dots (not before extension handled at segment level)
    s = re.sub(r"(?<=[^.])[.]+$", "", s)
    return s.strip()


def sanitize_segment(s: str | None) -> str:
    if not s:
        return ""
    s = replace_colon(s)
    s = replace_path_separators(s, " ")
    s = normalize_quotation_marks(s)
    s = trim_trailing_punctuation(s)
    s = validate_file_name(s)
    s = _INVISIBLE_PATH.sub("", s)
    return s.strip()


def truncate_title(title: str, limit: int = TITLE_MAX_LENGTH) -> str:
    if len(title) <= limit:
        return title
    words = title.split()
    out: list[str] = []
    n = 0
    for w in words:
        if n + len(w) + (1 if out else 0) >= limit:
            break
        out.append(w)
        n += len(w) + (1 if out else 0)
    return " ".join(out) if out else title[:limit]


def movie_name_with_year(title: str, year: int | None) -> str:
    t = sanitize_segment(title)
    if year and year > 0:
        return f"{t} ({year})"
    return t


def season_folder_name(season: int | None, special_ep: int | None) -> str:
    if special_ep is not None:
        return "Specials"
    if season is None:
        return "Season 00"
    if season == 0:
        return "Specials"
    return f"Season {season:02d}"


def episode_numbers_s00e00(season: int, episode: int) -> str:
    return f"S{season:02d}E{episode:02d}"


def _tmdb_folder_suffix(tmdb_id: int | None) -> str:
    """TMDB id suffix for primary movie/series folder only (brace form; hyphen, no colon)."""
    return f" {{tmdb-{tmdb_id}}}" if tmdb_id is not None else ""


# Current `{tmdb-<id>}` plus legacy `[tmdb-<id>]` for skip detection.
_TMDB_TAG_IN_SEGMENT = re.compile(r"\{tmdb-(?P<brace>\d+)\}|\[tmdb-(?P<bracket>\d+)\]")


def parse_tmdb_tag_from_path(path: Path) -> tuple[int, Literal["movie", "tv"]] | None:
    """
    If the path is under a Plex-style Movies/ or Series/ tree and a segment contains
    ``{tmdb-<id>}`` (or legacy ``[tmdb-<id>]``), return (id, kind). Otherwise None — including when
    both Movies and Series appear (ambiguous) or neither appears (avoid false positives).
    """
    parts = path.parts
    lower = [p.lower() for p in parts]
    has_movies = "movies" in lower
    has_series = "series" in lower
    if (has_movies and has_series) or not (has_movies or has_series):
        return None
    kind: Literal["movie", "tv"] = "movie" if has_movies else "tv"
    for part in parts:
        m = _TMDB_TAG_IN_SEGMENT.search(part)
        if m:
            sid = m.group("brace") or m.group("bracket")
            return (int(sid), kind)
    return None


def build_movie_dest(
    output_root: Path,
    title: str,
    year: int | None,
    source_file: Path,
    *,
    tmdb_movie_id: int | None = None,
) -> Path:
    ny = movie_name_with_year(title, year)
    file_stem = sanitize_segment(ny)
    folder = sanitize_segment(ny + _tmdb_folder_suffix(tmdb_movie_id))
    fname = sanitize_segment(file_stem + source_file.suffix)
    return output_root / "Movies" / folder / fname


def build_season_extra_dest(
    output_root: Path,
    series_name: str,
    season: int,
    source_file: Path,
    *,
    tmdb_tv_id: int | None = None,
    display_title: str | None = None,
) -> Path:
    """
    Extras / featurettes without SxxEyy: place under ``Series/<show {tmdb}>/Season NN/`` using a
    plain sanitized filename (no episode numbering).
    """
    ser = sanitize_segment(series_name)
    series_folder = sanitize_segment(ser + _tmdb_folder_suffix(tmdb_tv_id))
    if season == 0:
        sfold = "Specials"
    else:
        sfold = f"Season {season:02d}"
    stem = sanitize_segment(display_title or source_file.stem)
    fname = sanitize_segment(stem + source_file.suffix)
    return output_root / "Series" / series_folder / sfold / fname


def build_episode_dest(
    output_root: Path,
    series_name: str,
    season: int,
    episode: int,
    episode_title: str | None,
    source_file: Path,
    *,
    tmdb_tv_id: int | None = None,
) -> Path:
    ser = sanitize_segment(series_name)
    series_folder = sanitize_segment(ser + _tmdb_folder_suffix(tmdb_tv_id))
    if season == 0:
        sfold = "Specials"
        num_part = episode_numbers_s00e00(0, episode)
    else:
        sfold = f"Season {season:02d}"
        num_part = episode_numbers_s00e00(season, episode)
    et = truncate_title(sanitize_segment(episode_title or "Episode"))
    base = f"{ser} - {num_part} - {et}"
    fname = sanitize_segment(base + source_file.suffix)
    return output_root / "Series" / series_folder / sfold / fname
