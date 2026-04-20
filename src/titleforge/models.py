from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MediaKind = Literal["movie", "episode", "skipped"]


@dataclass
class PlanEntry:
    """One row in the rename plan after Phase 1."""

    src: Path
    dest: Path | None
    kind: MediaKind
    # For Modify → re-resolve from TMDB
    tmdb_movie_id: int | None = None
    tmdb_tv_id: int | None = None
    season: int | None = None
    episode: int | None = None
    note: str = ""


@dataclass
class RenamePlan:
    entries: list[PlanEntry] = field(default_factory=list)
