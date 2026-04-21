"""Single-entity TV pack detection (common root + season/extras layout)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from titleforge.classify import guess_kind, parse_sxe
from titleforge.series_folder import _SEASON_DIR, is_extras_parent_name


def _path_under_or_equal(ancestor: Path, path: Path) -> bool:
    """True if ``path`` is ``ancestor`` or a descendant of ``ancestor``."""
    a, p = ancestor.resolve(), path.resolve()
    if a == p:
        return True
    try:
        p.relative_to(a)
        return True
    except ValueError:
        return False


# First path segment under pack root must be season-like or extras parent (all → one show).
def first_segments_under(root: Path, files: list[Path]) -> set[str]:
    root = root.resolve()
    segs: set[str] = set()
    for f in files:
        try:
            rel = f.resolve().relative_to(root)
        except ValueError:
            return set()
        if rel.parts:
            segs.add(rel.parts[0])
    return segs


def content_root(files: list[Path], ceiling: Path | None = None) -> Path:
    """
    Deepest directory that contains all files and passes ``is_single_tv_pack``.

    Walks from ``os.path.commonpath`` (file parent if a single file was passed) up to
    ancestors and picks the matching path with the longest path (closest to the videos).

    If ``ceiling`` is set, never consider directories **above** it (the scan root, e.g.
    ``--input``), so a layout like ``Downloads/Torrents/Show`` cannot bind to ``Downloads``.
    """
    if not files:
        raise ValueError("content_root requires at least one file")
    paths = [str(f.resolve()) for f in files]
    c = Path(os.path.commonpath(paths))
    if c.is_file():
        c = c.parent
    c = c.resolve()
    ceiling_r = ceiling.resolve() if ceiling is not None else None
    candidates: list[Path] = []
    cur = c
    while True:
        cr = cur.resolve()
        if ceiling_r is not None and not _path_under_or_equal(ceiling_r, cr):
            break
        if is_single_tv_pack(files, cur):
            candidates.append(cr)
        parent = cur.parent.resolve()
        if parent == cur:
            break
        cur = parent
    if not candidates:
        return c
    return max(candidates, key=lambda p: len(p.parts))


def input_entity_for_path(input_root: Path, path: Path) -> Path:
    """
    Top-level folder under ``input_root`` that owns ``path`` (or ``input_root`` itself
    if the file lies directly in the input directory).
    """
    ir = input_root.resolve()
    p = path.resolve()
    rel = p.relative_to(ir)
    if not rel.parts:
        return ir
    return (ir / rel.parts[0]).resolve()


def entity_roots_under_input(files: list[Path], input_root: Path) -> list[Path]:
    """Sorted unique first-level entities under ``input_root`` that contain at least one file."""
    roots: dict[Path, None] = {}
    ir = input_root.resolve()
    for f in files:
        e = input_entity_for_path(ir, f)
        roots[e] = None
    return sorted(roots.keys(), key=lambda p: str(p).lower())


def _root_has_tv_signals(root: Path, files: list[Path]) -> bool:
    if any(parse_sxe(f) is not None for f in files):
        return True
    epish = sum(1 for f in files if guess_kind(f) == "episode")
    if epish >= max(1, (len(files) + 3) // 4):
        return True
    try:
        for p in root.iterdir():
            if not p.is_dir():
                continue
            if _SEASON_DIR.match(p.name) or is_extras_parent_name(p.name):
                return True
    except OSError:
        pass
    return False


def is_single_tv_pack(files: list[Path], root: Path) -> bool:
    """
    True when all files live under ``root`` and the tree looks like one TV pack
    (season folders and/or extras parents), not multiple top-level show folders.
    """
    if len(files) < 1:
        return False
    root = root.resolve()
    if root.parent == root:
        return False
    if _SEASON_DIR.match(root.name):
        return False
    segs = first_segments_under(root, files)
    segs.discard("")
    if not segs:
        return _root_has_tv_signals(root, files)
    if len(segs) == 1:
        return _root_has_tv_signals(root, files)
    for s in segs:
        if not (_SEASON_DIR.match(s) or is_extras_parent_name(s)):
            return False
    return True


_season_num = re.compile(r"(?i)^(?:season\s*(\d{1,4})|(s)(\d{1,4}))$")


def season_number_from_dir_name(name: str) -> int | None:
    m = _season_num.match(name.strip())
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    return int(m.group(3))


def infer_season_from_path_ancestors(path: Path, stop_at: Path) -> int | None:
    """
    Walk parents from ``path`` upward until ``stop_at`` (exclusive) and return
    the season number from the nearest ``Season N`` / ``Sn`` folder name.
    """
    cur = path.parent.resolve()
    stop = stop_at.resolve()
    while cur != stop and cur.name:
        n = season_number_from_dir_name(cur.name)
        if n is not None:
            return n
        nxt = cur.parent.resolve()
        if nxt == cur:
            break
        cur = nxt
    return None
