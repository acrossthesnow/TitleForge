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


def get_tmdb_api_key() -> str:
    """TMDB v3 API key or v4 read access token (JWT)."""
    key = _tmdb_key_from_environ()
    if not key:
        raise SystemExit(
            "Missing TMDB credentials. Set TMDB_API_KEY (v3 API key) or "
            "TMDB_READ_ACCESS_TOKEN (v4 JWT) in titleforge.conf or the environment — see README."
        )
    return key
