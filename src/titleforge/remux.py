"""Dolby Vision profile 7 → 8.1 lossless remux (see dv7-remux core spec).

DV profile 7 (dual-layer BL+EL from UHD Blu-ray rips) breaks playback in
streaming ecosystems. Converting to profile 8.1 is a lossless repackage: the
HEVC base layer is copied bit-for-bit, the RPU metadata is converted, and the
enhancement layer (which no consumer streaming device decodes) is dropped.

External tools: ffprobe + ffmpeg, dovi_tool
(https://github.com/quietvoid/dovi_tool), mkvmerge (mkvtoolnix).

This module is deliberately TUI-free: pure logic + subprocess, so it can be
unit-tested with mocks and reused outside the Textual review app. All failures
raise :class:`RemuxError` with a human-readable reason and leave the source
file untouched; temp files are cleaned up on every failure path.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# These are large files (up to ~100 GB, possibly over a network share). Never
# use short subprocess timeouts — bound each step generously instead.
STEP_TIMEOUT_SECONDS = 2 * 60 * 60
PROBE_TIMEOUT_SECONDS = 10 * 60

#: Verification: container duration of the remux must be within this many
#: seconds of the source's.
DURATION_TOLERANCE_SECONDS = 2.0

#: Basename of the intermediate converted-video stream in the work dir
#: (deleted as soon as mkvmerge finishes).
BL_RPU_FILENAME = "BL_RPU.hevc"

_TOOL_FIELDS = ("ffmpeg", "ffprobe", "dovi_tool", "mkvmerge")


class RemuxError(Exception):
    """A DV7 remux failed. The original file is untouched and as playable as before."""


@dataclass(frozen=True)
class RemuxTools:
    """Executable paths for the external tools (bare names resolve on PATH)."""

    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    dovi_tool: str = "dovi_tool"
    mkvmerge: str = "mkvmerge"


def check_tools(tools: RemuxTools) -> list[str]:
    """Return the tool names (attribute names) that can't be resolved to an executable."""
    return [name for name in _TOOL_FIELDS if shutil.which(getattr(tools, name)) is None]


def probe_video(path: Path, ffprobe_path: str = "ffprobe") -> dict[str, Any]:
    """ffprobe *path* and distill the fields used for detection and verification.

    Returns a dict with keys: ``dv_profile`` (int | None), ``r_frame_rate``
    (str | None, e.g. ``"24000/1001"``), ``duration`` (float | None, seconds),
    ``audio_count``, ``subtitle_count`` (int), ``title`` (str | None).

    Raises RemuxError if ffprobe fails or emits unparseable output.
    """
    cmd = [
        ffprobe_path,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemuxError(f"ffprobe failed for {path.name}: {exc}") from exc
    if proc.returncode != 0:
        raise RemuxError(
            f"ffprobe exited {proc.returncode} for {path.name}: {proc.stderr.strip()}"
        )
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RemuxError(f"ffprobe produced invalid JSON for {path.name}") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    dv_profile: int | None = None
    r_frame_rate: str | None = None
    if video is not None:
        r_frame_rate = video.get("r_frame_rate")
        for sd in video.get("side_data_list") or []:
            if sd.get("side_data_type") == "DOVI configuration record":
                dv_profile = sd.get("dv_profile")
                break

    fmt = data.get("format") or {}
    duration: float | None = None
    raw_duration = fmt.get("duration")
    if raw_duration is not None:
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            duration = None
    title = (fmt.get("tags") or {}).get("title")

    return {
        "dv_profile": dv_profile,
        "r_frame_rate": r_frame_rate,
        "duration": duration,
        "audio_count": sum(1 for s in streams if s.get("codec_type") == "audio"),
        "subtitle_count": sum(1 for s in streams if s.get("codec_type") == "subtitle"),
        "title": title,
    }


def is_dv7_mkv(path: Path, ffprobe_path: str = "ffprobe") -> bool:
    """True iff *path* is an ``.mkv`` whose first video stream is DV profile 7.

    Only ``.mkv`` inputs are in scope; any other dv_profile (5, 8) or no DV at
    all means the file is skipped untouched. Probe failures are logged and
    treated as "not DV7" — detection must never break the surrounding workflow.
    """
    path = Path(path)
    if path.suffix.lower() != ".mkv":
        return False
    try:
        return probe_video(path, ffprobe_path)["dv_profile"] == 7
    except RemuxError as exc:
        logger.warning("DV7 detection skipped for %s: %s", path, exc)
        return False


def remux_dv7_to_dv8(src: Path, out_path: Path, tools: RemuxTools) -> None:
    """Losslessly convert a DV profile 7 MKV to profile 8.1 at *out_path*.

    The work location is ``out_path.parent`` and must be on the same
    filesystem as the final output. The source file is never modified; the
    output is verified (DV profile 8, duration, stream counts) before this
    function returns. Any failure raises :class:`RemuxError` after removing
    all temp files.
    """
    src = Path(src)
    out_path = Path(out_path)
    work = out_path.parent
    bl_rpu = work / BL_RPU_FILENAME

    src_info = probe_video(src, tools.ffprobe)
    if src_info["dv_profile"] != 7:
        raise RemuxError(
            f"{src.name}: not DV profile 7 (dv_profile={src_info['dv_profile']}) — nothing to do"
        )
    fps = src_info["r_frame_rate"]
    if not fps:
        raise RemuxError(f"{src.name}: could not determine r_frame_rate for the remux")

    # Precondition: free space in the work dir >= source size (statvfs-backed).
    src_size = src.stat().st_size
    free = shutil.disk_usage(work).free
    if free < src_size:
        raise RemuxError(
            f"not enough free space in {work} ({free} bytes free, need {src_size}) — "
            f"skipping remux of {src.name}"
        )

    try:
        _extract_and_convert(src, bl_rpu, tools)
        _run_mkvmerge(src, bl_rpu, out_path, fps, src_info["title"], tools)
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        # Delete the intermediate stream as soon as mkvmerge finishes (or fails).
        bl_rpu.unlink(missing_ok=True)

    try:
        _verify_output(src, out_path, src_info, tools)
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise


def _extract_and_convert(src: Path, bl_rpu: Path, tools: RemuxTools) -> None:
    """ffmpeg (Annex-B HEVC to stdout) piped into dovi_tool convert.

    One streamed pass — avoids a second full copy of the video on disk.
    ``-m 2`` converts the RPU to profile 8.1; ``--discard`` drops the
    enhancement layer. Both processes' exit codes are checked (pipefail
    semantics).
    """
    ffmpeg_cmd = [
        tools.ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-i",
        str(src),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        "-f",
        "hevc",
        "-",
    ]
    dovi_cmd = [
        tools.dovi_tool,
        "-m",
        "2",
        "convert",
        "--discard",
        "-",
        "-o",
        str(bl_rpu),
    ]
    try:
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        raise RemuxError(f"could not start ffmpeg: {exc}") from exc
    try:
        dovi_proc = subprocess.Popen(
            dovi_cmd,
            stdin=ffmpeg_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        ffmpeg_proc.kill()
        ffmpeg_proc.wait()
        raise RemuxError(f"could not start dovi_tool: {exc}") from exc
    # Drop our handle so ffmpeg sees SIGPIPE if dovi_tool exits early.
    if ffmpeg_proc.stdout is not None:
        ffmpeg_proc.stdout.close()

    try:
        _, dovi_err = dovi_proc.communicate(timeout=STEP_TIMEOUT_SECONDS)
        _, ffmpeg_err = ffmpeg_proc.communicate(timeout=STEP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        for proc in (dovi_proc, ffmpeg_proc):
            proc.kill()
            proc.wait()
        raise RemuxError(f"extract+convert timed out for {src.name}") from exc

    if ffmpeg_proc.returncode != 0:
        raise RemuxError(
            f"ffmpeg video extraction failed (exit {ffmpeg_proc.returncode}) for "
            f"{src.name}: {_decode_stderr(ffmpeg_err)}"
        )
    if dovi_proc.returncode != 0:
        raise RemuxError(
            f"dovi_tool convert failed (exit {dovi_proc.returncode}) for "
            f"{src.name}: {_decode_stderr(dovi_err)}"
        )


def _run_mkvmerge(
    src: Path,
    bl_rpu: Path,
    out_path: Path,
    r_frame_rate: str,
    title: str | None,
    tools: RemuxTools,
) -> None:
    """Remux the converted stream with the source's audio/subs/chapters/attachments.

    ``--default-duration 0:<fps>p`` pins the frame rate of the raw HEVC track
    (raw Annex-B has no container timing); ``-D <src>`` takes everything except
    video from the source. Exit codes: 0 = ok, 1 = ok-with-warnings (logged),
    2 = failure.
    """
    cmd = [tools.mkvmerge, "-o", str(out_path)]
    if title:
        cmd += ["--title", title]
    cmd += [
        "--default-duration",
        f"0:{r_frame_rate}p",
        str(bl_rpu),
        "-D",
        str(src),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=STEP_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemuxError(f"mkvmerge failed for {src.name}: {exc}") from exc
    if proc.returncode == 1:
        logger.warning(
            "mkvmerge finished with warnings for %s:\n%s",
            src.name,
            (proc.stdout or proc.stderr).strip(),
        )
    elif proc.returncode != 0:
        raise RemuxError(
            f"mkvmerge failed (exit {proc.returncode}) for {src.name}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )


def _verify_output(
    src: Path, out_path: Path, src_info: dict[str, Any], tools: RemuxTools
) -> None:
    """Mandatory verification before the original is replaced/consumed."""
    out_info = probe_video(out_path, tools.ffprobe)
    if out_info["dv_profile"] != 8:
        raise RemuxError(
            f"verification failed for {src.name}: output dv_profile is "
            f"{out_info['dv_profile']}, expected 8"
        )
    src_dur, out_dur = src_info["duration"], out_info["duration"]
    if src_dur is None or out_dur is None:
        raise RemuxError(
            f"verification failed for {src.name}: could not compare container durations"
        )
    if abs(src_dur - out_dur) > DURATION_TOLERANCE_SECONDS:
        raise RemuxError(
            f"verification failed for {src.name}: duration drifted "
            f"({src_dur:.2f}s → {out_dur:.2f}s, tolerance {DURATION_TOLERANCE_SECONDS}s)"
        )
    for key, label in (("audio_count", "audio"), ("subtitle_count", "subtitle")):
        if src_info[key] != out_info[key]:
            raise RemuxError(
                f"verification failed for {src.name}: {label} stream count changed "
                f"({src_info[key]} → {out_info[key]})"
            )


def _decode_stderr(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return raw.strip()
