from __future__ import annotations

import re
from pathlib import Path

# Release / group noise (subset of FileBot stripReleaseInfo goals)
_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_RESOLUTION = re.compile(
    r"(?i)\b(720p|1080p|2160p|4k|web-?dl|webrip|bluray|bdrip|brrip|dvdrip|hdtv|"
    r"remux|x264|x265|hevc|h\.264|h\.265|av1|aac\d*\.?\d*|dts|truehd|atmos|ac3|"
    r"eac3|ddp?\d*\.?\d*|hdr\d*|sdr|uhd)\b"
)
_SEPARATORS = re.compile(r"[._]+")


def strip_release_info(name: str, aggressive: bool = True) -> str:
    """Strip typical scene/release tokens from a filename or folder name."""
    s = name
    if aggressive:
        s = _BRACKETED.sub(" ", s)
    s = _RESOLUTION.sub(" ", s)
    s = _SEPARATORS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def basename_terms(path: Path) -> list[str]:
    """Candidate query strings from file stem."""
    stem = path.stem
    out = [strip_release_info(stem, aggressive=True)]
    alt = strip_release_info(stem, aggressive=False)
    if alt != out[0]:
        out.append(alt)
    return [x for x in out if x]


def parent_folder_term(path: Path) -> str | None:
    parent = path.parent
    if parent == path.anchor or not parent.name:
        return None
    return strip_release_info(parent.name, aggressive=True)
