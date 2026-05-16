"""Rescue mode: move orphan subtitle/sidecar files left behind by a prior run.

Use case: TitleForge moved a movie ``Final.Fantasy.2001.mp4`` to
``Movies/Final Fantasy: The Spirits Within (2001) {tmdb-2114}/…`` but the
``Final.Fantasy.2001.srt`` sitting in the source folder was overlooked (or
the sidecar feature didn't exist yet). Re-running the normal flow does
nothing because ``discover_videos`` finds no videos in the source anymore.

Rescue mode:

1. Walks ``input_root`` for sidecar files (``.srt``/``.sub``/``.idx``/…).
2. Filters to **orphans** — sidecars with no video sibling whose stem they
   match (the video has been moved away).
3. Groups orphans by source folder.
4. Classifies each source folder by name (movie folder via
   :func:`clean_stem_for_search`), searches TMDB once, and only proceeds when
   the match is confident: exactly one result, or exactly one year-matching
   result. Ambiguous folders are reported and skipped to avoid attaching
   subtitles to the wrong movie.
5. Looks in ``output_root/Movies/`` for a folder whose name contains
   ``{tmdb-<id>}`` matching the resolved id, then finds the single video
   inside.
6. Moves each sidecar next to that video, renamed to ``<dest video stem><suffix>``
   where ``<suffix>`` is the part of the sidecar's name after the original
   video stem (preserves ``.en.forced.srt`` etc. via
   :func:`titleforge.sidecars.split_sidecar_basename`).

TV-show sidecars are out of scope here — episodes have per-file destinations,
not a single video per folder. The user can re-run when that's needed.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from titleforge.discover import VIDEO_EXTENSIONS
from titleforge.query_clean import clean_stem_for_search
from titleforge.sidecars import (
    SIDECAR_EXTENSIONS,
    split_sidecar_basename,
)
from titleforge.tmdb_errors import TmdbAuthError

if TYPE_CHECKING:
    # tmdb_client pulls in httpx — keep that out of the runtime import path so
    # rescue logic can be exercised in tests with a MagicMock.
    from titleforge.tmdb_client import TmdbClient


@dataclass
class RescueResult:
    moved: list[tuple[Path, Path]]
    unmatched: list[Path]   # source dirs we couldn't identify confidently


def rescue_orphan_sidecars(
    input_root: Path,
    output_root: Path,
    tmdb: "TmdbClient",
) -> RescueResult:
    """Move orphan sidecars from ``input_root`` to their videos under
    ``output_root``. See module docstring for the full algorithm."""
    moved: list[tuple[Path, Path]] = []
    unmatched: list[Path] = []
    orphans = _find_orphan_sidecars(input_root.resolve())
    if not orphans:
        return RescueResult(moved=moved, unmatched=unmatched)

    for source_dir, sidecars in sorted(orphans.items()):
        target = _resolve_source_folder_movie(source_dir, tmdb)
        if target is None:
            _notice(source_dir, "Rescue: could not confidently identify a movie; skipping.")
            unmatched.append(source_dir)
            continue
        tmdb_id, title = target
        dest_video = _find_dest_movie_video(output_root, tmdb_id)
        if dest_video is None:
            _notice(
                source_dir,
                f"Rescue: matched {title!r} ({{tmdb-{tmdb_id}}}) but no movie "
                f"folder with that id was found under {output_root}/Movies; skipping.",
            )
            unmatched.append(source_dir)
            continue
        for sc in sidecars:
            new_path = _move_sidecar_to_dest(sc, dest_video)
            if new_path is not None:
                moved.append((sc, new_path))
                print(
                    f"[SUB] {sc.name} → {new_path}",
                    file=sys.stderr,
                    flush=True,
                )
    return RescueResult(moved=moved, unmatched=unmatched)


def _notice(path: Path, message: str) -> None:
    print(f"TitleForge [{path.name}]: {message}", file=sys.stderr, flush=True)


def _find_orphan_sidecars(input_root: Path) -> dict[Path, list[Path]]:
    """Group sidecar files in ``input_root`` whose original video is no
    longer in the same directory, keyed by parent directory.
    """
    by_dir: dict[Path, list[Path]] = {}
    if not input_root.is_dir():
        return by_dir
    for p in input_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SIDECAR_EXTENSIONS:
            continue
        parent = p.parent
        if _has_matching_video_sibling(parent, p):
            continue
        by_dir.setdefault(parent.resolve(), []).append(p)
    return by_dir


def _has_matching_video_sibling(directory: Path, sidecar: Path) -> bool:
    """True if the directory contains a video file whose stem is a prefix of
    the sidecar's filename (the sidecar isn't an orphan)."""
    try:
        for sib in directory.iterdir():
            if not sib.is_file():
                continue
            if sib.suffix.lower().lstrip(".") not in VIDEO_EXTENSIONS:
                continue
            if sidecar.name.startswith(sib.stem):
                return True
    except OSError:
        return False
    return False


def _resolve_source_folder_movie(
    source_dir: Path, tmdb: TmdbClient
) -> tuple[int, str] | None:
    """Identify the movie this folder previously contained, conservatively.

    Returns ``(tmdb_movie_id, title)`` only when the match is unambiguous
    (single TMDB result, or single result matching the folder's year). Returns
    None otherwise — we don't want to attach subtitles to the wrong movie.
    """
    cleaned = clean_stem_for_search(source_dir.name)
    title = (cleaned.title or "").strip()
    year = cleaned.year
    if not title or year is None:
        return None
    try:
        results = tmdb.search_movie(title, year)
    except TmdbAuthError:
        raise
    except Exception:
        return None
    if not results:
        return None
    # Dedupe by id.
    seen: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for r in results:
        rid = r.get("id")
        if isinstance(rid, int) and rid not in seen:
            seen.add(rid)
            deduped.append(r)
    if len(deduped) == 1:
        r = deduped[0]
        return int(r["id"]), r.get("title") or r.get("original_title") or title
    # Multiple results — require a single year match before committing.
    year_matches = [r for r in deduped if _year_of(r) == year]
    if len(year_matches) == 1:
        r = year_matches[0]
        return int(r["id"]), r.get("title") or r.get("original_title") or title
    return None


def _year_of(row: dict[str, Any]) -> int | None:
    rd = row.get("release_date") or ""
    if len(rd) >= 4 and rd[:4].isdigit():
        return int(rd[:4])
    return None


def _find_dest_movie_video(output_root: Path, tmdb_id: int) -> Path | None:
    """Look up ``output_root/Movies/<…{tmdb-N}…>/<single video>``."""
    movies_dir = output_root / "Movies"
    if not movies_dir.is_dir():
        return None
    tag = f"{{tmdb-{tmdb_id}}}"
    for folder in movies_dir.iterdir():
        if not folder.is_dir():
            continue
        if tag not in folder.name:
            continue
        for f in folder.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower().lstrip(".") in VIDEO_EXTENSIONS:
                return f
    return None


def _move_sidecar_to_dest(sidecar: Path, dest_video: Path) -> Path | None:
    """Move ``sidecar`` next to ``dest_video`` with the renamed stem. Returns
    the new path or None when the move was skipped (overwrite refused / error).
    """
    _, suffix = split_sidecar_basename(sidecar.name)
    new_path = dest_video.parent / (dest_video.stem + suffix)
    if new_path.exists():
        return None
    try:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sidecar), str(new_path))
    except OSError:
        return None
    return new_path
