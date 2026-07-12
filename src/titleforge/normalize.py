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
    r"web-?dl|web-?rip|webrip|blu[._-]?ray|bd-?rip|br-?rip|bd-?remux|dvd-?rip|dvd5|dvd9|"
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
    # Audio language indicators. Scene encoders insert these between the
    # resolution and source (Pantheon.S01E01.1080p.HIDI.WEB-DL.…) — without
    # them in the strip list the language tag leaks into the TMDB query.
    # Restricted to multi-char codes (ENG/JPN/HINDI/MULTI/…) so two-letter
    # title fragments (IT, MA, EN, ES, …) stay intact.
    r"hindi|hidi|eng|esp|esub|fre|fra|ger|deu|"
    r"ita|jpn|kor|chi|cht|chs|rus|por|spa|swe|nor|dan|fin|nld|tur|"
    r"multi(?:[._-]?\d+)?|dual(?:[._-]?audio)?|dub(?:bed)?|subbed|"
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


# --- Title-prefix extraction -------------------------------------------------
#
# Real-world names put the title FIRST and the decoration after it, so the most
# robust query is "everything before the first junk signal", not "whatever
# survives junk deletion". The token regexes above (_PACK_RANGE / _RESOLUTION)
# are reused here as boundary *detectors*: unknown junk after the first
# recognized boundary costs nothing, which is what makes this degrade well on
# folder names we've never seen.

# Bracketed group that opens with a year — "(2000-2004)", "[1999]", "{2012".
_BRACKET_YEAR = re.compile(r"[(\[{]\s*(?:19|20)\d{2}")
# Bare season markers incl. ranges ("S01", "S01-S04"). \b keeps "S1m0ne" intact.
_SEASON_TOKEN = re.compile(r"(?i)\bS\d{1,4}\b")
# Bare "Complete" ("Complete ANIMATED TV Series") — _PACK_RANGE only catches it
# when directly followed by series/show/pack/collection.
_COMPLETE_WORD = re.compile(r"(?i)\bcomplete\b")
# Leading enumeration on curated packs: "1. The ZETA Project - …" and lettered
# subfolders "a. Season 1 (1999)" / "e. Crossovers (2001-05)". Requires the
# separator punctuation AND whitespace so "24 - S01E01" / "9.S01E01" survive.
# Known trade-off: a title written as "B. The Beginning" loses its "B. " — rare
# vs. the enumerated-folder uploads seen in the wild, and Phase 1.5 catches it.
_LEAD_ENUM = re.compile(r"^\s*(?:\d{1,3}|[A-Za-z])\s*[.)]\s+")
# Leading square-bracket release group ("[Judas] Show …"). Square brackets only:
# leading parens can be a real title ("(500) Days of Summer").
_LEAD_SQ_GROUP = re.compile(r"^\s*\[[^\]]*\]\s*")

_TITLE_BOUNDARIES = (_PACK_RANGE, _RESOLUTION, _BRACKET_YEAR, _SEASON_TOKEN, _COMPLETE_WORD)

# Chars that count as "dangling separator" when left at either end after junk
# removal ("STATIC SHOCK - " → "STATIC SHOCK").
_DANGLING_EDGE = " \t-–—_,&+:;."


# Bracketed group that starts with a year: "(2002)", "(2001-05)", "[1999] HD".
_YEAR_GROUP = re.compile(r"[(\[{]\s*(?:19|20)\d{2}[^)\]}]*[)\]}]")


def strip_leading_enum(name: str) -> str:
    """Drop a leading list-enumeration prefix: ``"1. Show"`` / ``"e. Crossovers"``."""
    return _LEAD_ENUM.sub("", name)


def strip_year_groups(s: str) -> str:
    """Remove bracketed groups that open with a year — ``"Firefly (2002) - "``
    → ``"Firefly  - "`` — while leaving title parens like ``"(Unlimited)"``."""
    return _YEAR_GROUP.sub(" ", s)


def trim_stranded_separators(s: str) -> str:
    """Collapse separator runs left behind by token removal and strip dangling
    edge punctuation: ``"STATIC SHOCK - - "`` → ``"STATIC SHOCK"``."""
    s = re.sub(r"(?:\s*[-–—]\s*){2,}", " - ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(_DANGLING_EDGE)


def title_prefix(name: str) -> str:
    """Title text before the first junk boundary in a folder-ish name.

    ``"STATIC SHOCK (2000-2004) - Complete ANIMATED …"`` → ``"STATIC SHOCK"``;
    ``"Pantheon.S01.1080p.HIDI.WEB-DL"`` → ``"Pantheon"``. Names with no
    recognized junk pass through cleaned but whole. Returns ``""`` when the
    name *starts* with junk (e.g. ``"SEASON 1 (2000-2001)"``) — callers fall
    back to their legacy cleaning or a filename-derived query.
    """
    s = strip_leading_enum(name)
    while True:
        stripped = _LEAD_SQ_GROUP.sub("", s)
        if stripped == s:
            break
        s = stripped
    cut = len(s)
    for rx in _TITLE_BOUNDARIES:
        m = rx.search(s)
        if m is not None and m.start() < cut:
            cut = m.start()
    prefix = strip_release_info(s[:cut], aggressive=True)
    return trim_stranded_separators(prefix)


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
