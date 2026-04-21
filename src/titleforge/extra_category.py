"""Map source paths / names to Plex local-extras folder names (TV season extras)."""

from __future__ import annotations

import re
from pathlib import Path

# Plex "Organized in Subdirectories" names (movies); same layout is used under each season.
PLEX_EXTRA_FOLDERS = (
    "Behind The Scenes",
    "Deleted Scenes",
    "Featurettes",
    "Interviews",
    "Scenes",
    "Shorts",
    "Trailers",
    "Other",
)

_SEASON_SKIP = re.compile(r"(?i)^(season\s*\d+|s\d{1,4}|specials)$")

# Plex movie inline suffix (hyphen + type at end of stem), case-insensitive.
_PLEX_INLINE_SUFFIX = re.compile(
    r"(?i)-(?P<tag>behindthescenes|deleted|featurette|interview|scene|short|trailer|other)$"
)

_INLINE_TAG_TO_FOLDER: dict[str, str] = {
    "behindthescenes": "Behind The Scenes",
    "deleted": "Deleted Scenes",
    "featurette": "Featurettes",
    "interview": "Interviews",
    "scene": "Scenes",
    "short": "Shorts",
    "trailer": "Trailers",
    "other": "Other",
}


def _normalize_segment(name: str) -> str:
    s = name.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", s.strip()).casefold()


# Normalized path segment -> Plex folder (non-mapped extras use Other).
_SEGMENT_TO_FOLDER: dict[str, str] = {}


def _reg(synonyms: list[str], folder: str) -> None:
    for s in synonyms:
        _SEGMENT_TO_FOLDER[_normalize_segment(s)] = folder


_reg(
    [
        "behind the scenes",
        "behindthescenes",
        "behind the scene",
        "bts",
    ],
    "Behind The Scenes",
)
_reg(["deleted scenes", "deleted scene", "deletedscenes", "deleted"], "Deleted Scenes")
_reg(["featurettes", "featurette"], "Featurettes")
_reg(["interviews", "interview"], "Interviews")
_reg(["scenes", "scene"], "Scenes")
_reg(["shorts", "short"], "Shorts")
_reg(["trailers", "trailer"], "Trailers")
_reg(["clips", "clip"], "Other")
_reg(["samples", "sample"], "Other")
_reg(["extras", "extra"], "Other")
_reg(["other"], "Other")
_reg(["theme music", "thememusic", "theme-music", "theme song", "themesong"], "Other")
_reg(["backdrops", "backdrop"], "Other")
_reg(["bonus", "bonuses"], "Other")

for _name in PLEX_EXTRA_FOLDERS:
    _SEGMENT_TO_FOLDER.setdefault(_normalize_segment(_name), _name)


def all_extras_container_normalized() -> frozenset[str]:
    """Normalized folder names treated as extras containers (pack layout + series root walk)."""
    return frozenset(_SEGMENT_TO_FOLDER.keys())


def infer_plex_extra_folder(path: Path, *, entity_root: Path) -> str:
    """
    Infer Plex extras subdirectory under ``Season NN`` / ``Specials`` from ancestor folder
    names (nearest wins), else Plex inline ``-trailer`` / ``-featurette`` / … suffix on the
    stem, else ``Other``. Never looks above ``entity_root``.
    """
    ent = entity_root.resolve()
    cur = path.parent.resolve()
    stop = ent

    while cur != stop and cur.name:
        seg = cur.name
        if not _SEASON_SKIP.match(seg):
            hit = _SEGMENT_TO_FOLDER.get(_normalize_segment(seg))
            if hit is not None:
                return hit
        nxt = cur.parent.resolve()
        if nxt == cur:
            break
        cur = nxt

    stem = path.stem
    m = _PLEX_INLINE_SUFFIX.search(stem)
    if m:
        tag = m.group("tag").lower()
        return _INLINE_TAG_TO_FOLDER.get(tag, "Other")

    return "Other"
