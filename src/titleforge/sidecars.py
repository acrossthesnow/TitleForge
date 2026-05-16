"""Locate and move subtitle / index sidecar files alongside their video.

Plex (and most media players) auto-load sidecar files that sit next to a video
with a matching basename, so a renamed video without its `.srt` loses its
subtitles. This module finds the sidecars that belong to a given video and
computes their post-rename destination, preserving the language and `.forced`
tags after the basename.

Examples (video stem ``Final.Fantasy.2001``):

  Final.Fantasy.2001.srt              → <new>.srt
  Final.Fantasy.2001.en.srt           → <new>.en.srt
  Final.Fantasy.2001.en.forced.srt    → <new>.en.forced.srt
  Final.Fantasy.2001.eng.idx          → <new>.eng.idx
"""

from __future__ import annotations

from pathlib import Path

# Common Plex-compatible sidecar formats. SRT/SUB/IDX/VTT/SUP for subtitles,
# ASS/SSA for advanced styled subs.
SIDECAR_EXTENSIONS = frozenset(
    {".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".sup"}
)


def find_sidecars(video: Path) -> list[Path]:
    """Return sidecar files in ``video``'s directory whose name starts with the
    video's stem and ends in a known sidecar extension. Empty list if the
    video's directory can't be read or no sidecars are present.
    """
    parent = video.parent
    stem = video.stem
    if not parent.is_dir():
        return []
    out: list[Path] = []
    try:
        for entry in parent.iterdir():
            if not entry.is_file():
                continue
            if entry == video:
                continue
            if not entry.name.startswith(stem):
                continue
            if entry.suffix.lower() not in SIDECAR_EXTENSIONS:
                continue
            # The portion between the video stem and the file extension is the
            # language / `.forced` tag we want to preserve (or empty for the
            # default-language sidecar). Sanity-check by ensuring it starts
            # with `.` so we don't pick up unrelated files that just happen to
            # share a prefix.
            suffix_after_stem = entry.name[len(stem):]
            if not suffix_after_stem.startswith("."):
                continue
            out.append(entry)
    except OSError:
        return []
    return out


def sidecar_dest(sidecar: Path, video: Path, video_dest: Path) -> Path:
    """Compute the destination path for a sidecar that's being moved alongside
    ``video`` to ``video_dest``. Preserves whatever follows the video stem in
    the sidecar's filename — including multi-dot suffixes like ``.en.forced.srt``.
    """
    suffix_after_stem = sidecar.name[len(video.stem):]
    return video_dest.parent / (video_dest.stem + suffix_after_stem)
