"""Detect TV series pack folders so we key one TMDB series lookup per group."""

from __future__ import annotations

import re
from pathlib import Path

from titleforge.classify import parse_sxe
from titleforge.extra_category import all_extras_container_normalized

# Folder name hints: "Season 1", "S01", "Complete Series", etc.
_SEASON_DIR = re.compile(r"(?i)^(season\s*\d+|s\d+)$")
_SERIES_WORDS = re.compile(r"(?i)\b(season|series|complete|volume|vol\.?)\b")

_EXTRAS_PARENT_NORMALIZED = all_extras_container_normalized()


def _normalize_folder_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()


def is_extras_parent_name(name: str) -> bool:
    """True if ``name`` is a common extras / featurettes container folder."""
    return _normalize_folder_name(name) in _EXTRAS_PARENT_NORMALIZED


def _walk_past_extras_containers(p: Path) -> Path:
    """Ascend past Featurettes / Extras / … so the series root is the real show folder."""
    cur = p.resolve()
    while cur.name and is_extras_parent_name(cur.name):
        parent = cur.parent.resolve()
        if parent == cur:
            break
        cur = parent
    return cur


def _siblings_same_parent(path: Path, all_files: list[Path]) -> list[Path]:
    d = path.parent.resolve()
    return [f for f in all_files if f.parent.resolve() == d]


def series_group_root(path: Path, all_files: list[Path]) -> Path | None:
    """
    Return the folder path used to share one TMDB series identity for grouped episodes.

    - If the parent looks like ``Season 1`` / ``S01``, the group key is the **grandparent**
      (show folder).
    - Otherwise if the parent looks like a pack (keywords or ≥2 parsable episodes), key is
      the **parent**.
    """
    parent = path.parent.resolve()
    if not parent.name:
        return None
    name = parent.name
    sibs = _siblings_same_parent(path, all_files)
    ep_like = sum(1 for f in sibs if parse_sxe(f) is not None)

    if _SEASON_DIR.match(name):
        gp = parent.parent
        if gp != parent.anchor and gp.name:
            gp = _walk_past_extras_containers(gp)
            return gp.resolve()
        return parent

    if ep_like >= 2:
        return parent

    if _SERIES_WORDS.search(name):
        return parent

    return None


def is_series_pack_folder(path: Path, all_files: list[Path]) -> bool:
    return series_group_root(path, all_files) is not None
