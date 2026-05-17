from __future__ import annotations

import re
from pathlib import Path

# Release / group noise (subset of FileBot stripReleaseInfo goals).
_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")

# Pack-range words removed BEFORE _RESOLUTION so that "Season 1 to 4" disappears
# as a unit; otherwise the digits and the lowercase `to` get stranded as tokens
# after _RESOLUTION runs.
_PACK_RANGE = re.compile(
    r"(?i)\b("
    r"complete\s*(?:series|show|pack|collection)|"
    r"seasons?\s*\d+(?:\s*(?:-|–|to)\s*\d+)?"
    r")\b"
)

# Resolutions, sources, codecs, audio, streaming providers, container tokens.
# Container tokens (mp4/mkv/...) are safe because every caller passes a stem or
# folder name — never a filename with an extension still on it.
_RESOLUTION = re.compile(
    r"(?i)\b("
    # Resolution / fidelity
    r"480p|576p|720p|1080p|1440p|2160p|4320p|4k|8k|uhd|sdr|"
    # HDR / dynamic range
    r"hdr10\+?|hdr\d*|dovi|dv|"
    # Source / format
    r"web-?dl|web-?rip|webrip|bluray|bdrip|brrip|bdremux|dvdrip|dvd5|dvd9|"
    r"hdtv|hdrip|remux|"
    # Codec
    r"x\.?\s*264|x\.?\s*265|h\.?\s*264|h\.?\s*265|hevc|avc|av1|vp9|"
    r"10-?bit|10\s*bit|"
    # Audio: cover DD2.0, DD.5.1, DDP5.1, DD+5.1 as single tokens so stripping
    # them doesn't leave orphan digit fragments behind.
    r"ddp?\+?\.?\s*\d(?:\.\d)?|aac\d*\.?\d*|aac|"
    r"dts-?hd(?:-?ma)?|dts-?x|dts|"
    r"e-?ac-?3|eac3|ac-?3|ac3|truehd|atmos|flac|opus|mp3|"
    # Streaming sources. Restricted to 3+ char unambiguous abbreviations so we
    # don't clobber real titles like "MA" (2019) or "IT" (2017). NF is the
    # exception — too common in scene names to drop, and matching only on word
    # boundary keeps the false-positive rate negligible.
    r"amzn|aptv|atvp|hmax|hulu|itunes|pcok|pmtp|stan|starz|strz|crave|dsnp|"
    r"nf|"
    # Generic release markers
    r"repack|proper|multi|extended|unrated|imax|open[._-]?matte|"
    # Container tokens (callers pass stem/folder, never the actual extension)
    r"mp4|mkv|avi|mov|m4v"
    r")\b"
)

_SEPARATORS = re.compile(r"[._]+")

# Trailing scene-group tag (e.g. " -RARBG", " -SiGMA"). Requires whitespace before
# the dash so legitimate hyphenated title fragments like "Spider-Man" are left
# alone — release tools always surface the group after a separator that's just
# been converted to whitespace by _SEPARATORS.
_SCENE_TAIL = re.compile(r"\s+-[A-Za-z0-9]{2,12}(?=\s|$)")


def strip_release_info(name: str, aggressive: bool = True) -> str:
    """Strip typical scene/release tokens from a filename or folder name."""
    s = name
    if aggressive:
        s = _BRACKETED.sub(" ", s)
    s = _PACK_RANGE.sub(" ", s)
    s = _RESOLUTION.sub(" ", s)
    s = _SEPARATORS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Iteratively peel trailing "-WORD" scene-group tails; chained tails
    # ("-RARBG-EXTRA") fall in successive passes.
    while True:
        new = re.sub(r"\s+", " ", _SCENE_TAIL.sub(" ", s)).strip()
        if new == s:
            break
        s = new
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
