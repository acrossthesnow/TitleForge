from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env(project_root: Path | None = None) -> None:
    load_dotenv(project_root / ".env" if project_root else None)


def get_tmdb_api_key() -> str:
    """TMDB v3 API key or v4 read access token (JWT)."""
    key = (
        os.environ.get("TMDB_READ_ACCESS_TOKEN", "").strip()
        or os.environ.get("TMDB_API_KEY", "").strip()
    )
    if not key:
        raise SystemExit(
            "Missing TMDB credentials. Set TMDB_API_KEY (v3 API key) or "
            "TMDB_READ_ACCESS_TOKEN (v4 JWT) in .env — see README."
        )
    return key
