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

# ISO-639 codes Plex commonly recognizes as the language tag in a Plex sidecar
# filename (Title.<lang>[.<mod>].ext). Used only to split an orphan sidecar's
# name into stem + suffix when we don't have the original video to compare
# against — see :func:`split_sidecar_basename`.
_KNOWN_LANG_CODES = frozenset(
    {
        "en", "eng", "es", "spa", "fr", "fra", "fre", "de", "deu", "ger",
        "ja", "jpn", "it", "ita", "pt", "por", "nl", "dut", "nld",
        "zh", "chi", "zho", "cmn", "ko", "kor", "ar", "ara", "ru", "rus",
        "pl", "pol", "tr", "tur", "sv", "swe", "no", "nor", "da", "dan",
        "fi", "fin", "el", "ell", "gre", "he", "heb", "hi", "hin",
        "id", "ind", "th", "tha", "vi", "vie", "uk", "ukr", "cs", "ces", "cze",
        "ro", "ron", "rum", "hu", "hun", "bg", "bul", "et", "est",
        "lv", "lav", "lt", "lit", "sk", "slk", "slo", "sl", "slv", "sr", "srp",
        "hr", "hrv", "ms", "msa", "may", "fa", "per", "fas", "ur", "urd",
        "tl", "tgl", "ca", "cat", "bn", "ben", "ta", "tam", "te", "tel",
    }
)

# Plex sidecar accessibility / variant tags that may follow the language code.
_KNOWN_MODIFIERS = frozenset({"forced", "sdh", "cc", "hi", "default"})


def split_sidecar_basename(name: str) -> tuple[str, str]:
    """Split a sidecar filename into ``(video_stem, suffix_after_stem)``.

    Walks dot-separated tokens from the right and consumes the extension plus
    any contiguous known language codes / modifiers into the suffix. Returns
    ``(stem, "." + ".".join(suffix_tokens))`` such that
    ``stem + suffix == name``. Used by the rescue tool when the original
    video has already been moved and we can't read its stem off disk.

    Examples::

        split_sidecar_basename("Foo.Bar.2001.srt")
            → ("Foo.Bar.2001", ".srt")
        split_sidecar_basename("Foo.Bar.2001.en.srt")
            → ("Foo.Bar.2001", ".en.srt")
        split_sidecar_basename("Foo.Bar.2001.en.forced.srt")
            → ("Foo.Bar.2001", ".en.forced.srt")
        split_sidecar_basename("Foo.Bar.2001.1080p.YIFY.srt")
            → ("Foo.Bar.2001.1080p.YIFY", ".srt")  # no lang/mod tokens
    """
    parts = name.split(".")
    if len(parts) < 2:
        return name, ""
    suffix_parts = [parts[-1]]
    i = len(parts) - 2
    while i > 0:
        tok = parts[i].lower()
        if tok in _KNOWN_MODIFIERS or tok in _KNOWN_LANG_CODES:
            suffix_parts.insert(0, parts[i])
            i -= 1
        else:
            break
    stem = ".".join(parts[: i + 1])
    suffix = "." + ".".join(suffix_parts)
    return stem, suffix


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
