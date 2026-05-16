from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

MediaKind = Literal["movie", "episode", "extra", "skipped"]
EntityKind = Literal["movie", "tv", "skipped"]
ConfidenceLevel = Literal["high", "medium", "low"]


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
    # The EntityLabel this row belongs to. Files under a pack-TV or movie-folder
    # binding share one key (the entity dir). Per-file resolutions key off the
    # file path itself. Filled in by build_plan() so the search-review UI can
    # group rows without re-deriving entity ownership.
    entity_key: Path | None = None


@dataclass
class EntityLabel:
    """One row in the Phase 1.5 search-review UI.

    Represents "one TMDB-id-bearing thing": either a top-level entity folder
    bound to a single movie / show, or a loose video file that resolved on its
    own. Several PlanEntries may share one label (a show's episodes + extras,
    or a movie folder's main video + featurettes).
    """

    key: Path                       # entity folder, or the lone file's path
    display_name: str               # entity.name (folder basename) or file.name
    kind: EntityKind
    tmdb_id: int | None
    title: str
    year: int | None
    confidence: ConfidenceLevel
    reason: str                     # short human note (e.g. "single year match", "ambiguous", "no TMDB hits")
    file_count: int
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RenamePlan:
    entries: list[PlanEntry] = field(default_factory=list)
    labels: list[EntityLabel] = field(default_factory=list)
