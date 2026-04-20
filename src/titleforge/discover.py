from __future__ import annotations

import re
from pathlib import Path

# Junk filenames (readme, nfo-as-md, double extensions) — avoid `.m4v` false positives
_JUNK_TXT_MD = re.compile(r"(?i)\.(txt|md)(\.|$)")

# Mirrors FileBot MediaTypes video category (MediaTypes.properties)
VIDEO_EXTENSIONS = frozenset(
    {
        "avi",
        "mkv",
        "mk3d",
        "ogm",
        "ogg",
        "mp4",
        "m4v",
        "3gp",
        "mov",
        "divx",
        "mpg",
        "mpeg",
        "vob",
        "ts",
        "tp",
        "m2ts",
        "rec",
        "wmv",
        "asf",
        "wtv",
        "dvr-ms",
        "webm",
        "flv",
        "rm",
        "rmvb",
        "rmp4",
        "tivo",
        "nuv",
        "3dsbs",
        "3dtab",
        "strm",
        "iso",
    }
)


def is_sample_path(path: Path) -> bool:
    p = str(path).lower()
    return "sample" in path.name.lower() and path.suffix.lower().lstrip(".") in VIDEO_EXTENSIONS


def is_junk_txt_md_path(path: Path) -> bool:
    """True if basename suggests .txt/.md junk (boundary-safe for normal video extensions)."""
    name = path.name
    if _JUNK_TXT_MD.search(name):
        return True
    stem = path.stem.lower()
    if stem.endswith(".txt") or stem.endswith(".md"):
        return True
    return False


def discover_videos(root: Path) -> list[Path]:
    """Recursive unique video files under root, sorted by path."""
    root = root.resolve()
    seen: set[Path] = set()
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower().lstrip(".")
        if ext not in VIDEO_EXTENSIONS:
            continue
        if is_junk_txt_md_path(p):
            continue
        if is_sample_path(p):
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        out.append(rp)
    out.sort(key=lambda x: str(x).lower())
    return out
