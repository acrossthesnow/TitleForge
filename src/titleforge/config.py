from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

CONFIG_FILENAME = "titleforge.conf"


def user_config_dir() -> Path:
    """Per-user config directory (XDG-style on Unix, %APPDATA% on Windows)."""
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "TitleForge"
        return Path.home() / "AppData" / "Roaming" / "TitleForge"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "titleforge"
    return Path.home() / ".config" / "titleforge"


def user_config_file() -> Path:
    return user_config_dir() / CONFIG_FILENAME


def _tmdb_key_from_environ() -> str:
    return (
        os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
        or os.environ.get("TMDB_API_KEY", "").strip()
    )


def _tmdb_credentials_present() -> bool:
    return bool(_tmdb_key_from_environ())


def load_dotenv_sources() -> None:
    """Load user-level config, then cwd titleforge.conf (override)."""
    load_dotenv(user_config_file())
    load_dotenv(Path.cwd() / CONFIG_FILENAME, override=True)


def _write_user_config_file(content: str) -> None:
    path = user_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)


def ensure_tmdb_credentials_interactive() -> None:
    """Prompt and write user titleforge.conf when missing credentials (TTY only)."""
    if _tmdb_credentials_present():
        return
    cfg_path = user_config_file().resolve()
    if not sys.stdin.isatty():
        raise SystemExit(
            "Missing TMDB credentials. Set environment variable TMDB_API_KEY (v3 key) or "
            "TMDB_READ_ACCESS_TOKEN (v4 JWT), or create the config file at:\n"
            f"  {cfg_path}\n"
            "See README for details."
        )
    import questionary

    user_config_dir().mkdir(parents=True, exist_ok=True)
    raw = questionary.password(
        "TMDB v3 API key or v4 read access token (JWT):"
    ).unsafe_ask(patch_stdout=True)
    if raw is None:
        raise SystemExit("Cancelled — no TMDB credentials provided.")
    key = raw.strip()
    if not key:
        raise SystemExit("Cancelled — no TMDB credentials provided.")
    _write_user_config_file(f"TMDB_API_KEY={key}\n")
    load_dotenv(cfg_path, override=True)


_TRUTHY = {"1", "true", "yes", "on"}


def get_convert_for_streaming_enabled() -> bool:
    """Config default for streaming-compatibility conversion (``CONVERT_FOR_STREAMING=true``).

    Umbrella key — currently this means the DV7→DV8.1 remux. The CLI flags
    ``--convert-for-streaming`` / ``--no-convert-for-streaming`` win over this
    value. Reads the environment populated by :func:`load_dotenv_sources`
    (user-level titleforge.conf, then cwd override, then real env vars).
    """
    return os.environ.get("CONVERT_FOR_STREAMING", "").strip().lower() in _TRUTHY


def get_remux_tools():
    """Tool paths for the DV7 remux pipeline (``FFMPEG_PATH`` etc.).

    Defaults to bare executable names resolved on PATH.
    """
    from titleforge.remux import RemuxTools

    return RemuxTools(
        ffmpeg=os.environ.get("FFMPEG_PATH", "").strip() or "ffmpeg",
        ffprobe=os.environ.get("FFPROBE_PATH", "").strip() or "ffprobe",
        dovi_tool=os.environ.get("DOVI_TOOL_PATH", "").strip() or "dovi_tool",
        mkvmerge=os.environ.get("MKVMERGE_PATH", "").strip() or "mkvmerge",
    )


def get_tmdb_api_key() -> str:
    """TMDB v3 API key or v4 read access token (JWT)."""
    key = _tmdb_key_from_environ()
    if not key:
        raise SystemExit(
            "Missing TMDB credentials. Set TMDB_API_KEY (v3 API key) or "
            "TMDB_READ_ACCESS_TOKEN (v4 JWT) in titleforge.conf or the environment — see README."
        )
    return key
