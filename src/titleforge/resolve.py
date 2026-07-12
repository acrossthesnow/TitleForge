from __future__ import annotations

import difflib
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

import questionary
from questionary import Style

from titleforge.classify import (
    _S00E00,
    guess_kind,
    looks_episode,
    looks_movie,
    parse_sxe,
    series_prefix_from_stem,
    series_query_string,
)
from titleforge.extra_category import infer_plex_extra_folder
from titleforge.models import ConfidenceLevel, EntityLabel, PlanEntry, RenamePlan
from titleforge.nfo import collect_ids_near_video
from titleforge.normalize import (
    basename_terms,
    parent_folder_term,
    strip_leading_enum,
    strip_release_info,
    title_prefix,
    trim_stranded_separators,
)
from titleforge.pack import (
    entity_roots_under_input,
    infer_season_from_path_ancestors,
    input_entity_for_path,
    is_single_tv_pack,
)
from titleforge.plex_paths import (
    build_episode_dest,
    build_movie_dest,
    build_season_extra_dest,
    movie_name_with_year,
    parse_tmdb_tag_from_path,
    sanitize_segment,
)
from titleforge.prompt_ui import LIST_STYLE, SearchType, clear_tty, prompt_search_with_type
from titleforge.query_clean import CleanedQuery, clean_stem_for_search
from titleforge.series_folder import is_extras_parent_name, is_series_pack_folder, series_group_root
from titleforge.tmdb_client import TmdbClient
from titleforge.tmdb_errors import TmdbAuthError


@dataclass
class _PerFileLabel:
    """Confidence + candidates harvested per per-file resolution, consumed when
    building the final EntityLabel list. Internal to resolve.py."""

    kind: Literal["movie", "tv", "skipped"]
    tmdb_id: int | None
    title: str
    year: int | None
    confidence: ConfidenceLevel
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PackTvBinding:
    """One TMDB show pick bound to a top-level entity folder (pack-TV pre-resolve)."""

    tmdb_tv_id: int
    series_name: str
    year: int | None
    confidence: ConfidenceLevel
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MovieEntityBinding:
    """One TMDB movie pick bound to a top-level entity folder (movie-folder pre-resolve)."""

    tmdb_movie_id: int
    title: str
    year: int | None
    confidence: ConfidenceLevel
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanContext:
    all_files: list[Path]
    series_by_root: dict[Path, tuple[int, str]] = field(default_factory=dict)
    season_cache: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    # Resolved ``--input``; pack TV binds only per first-level folder beneath it.
    input_root: Path | None = None
    # Top-level entity dir -> PackTvBinding from one pack pick per folder.
    entity_packs: dict[Path, PackTvBinding] = field(default_factory=dict)
    # Top-level entity dir -> single TMDB movie bound for the folder.
    entity_movies: dict[Path, MovieEntityBinding] = field(default_factory=dict)
    # Per-PlanEntry confidence/reason/candidates, keyed by source path. Populated
    # by the silent resolvers and harvested into EntityLabels at the end of
    # build_plan(). Avoids growing PlanEntry's surface area when the data is
    # really only needed for the search-review UI.
    per_file_label: dict[Path, "_PerFileLabel"] = field(default_factory=dict)
    # tv_id -> first-air year, captured wherever we already fetch tv_detail. Read
    # by _finalize_episode so files #2..#N of a pack (which short-circuit the
    # search via series_by_root) can render `Series (YYYY)` on their label
    # without an extra TMDB round-trip.
    series_year_by_tv_id: dict[int, int | None] = field(default_factory=dict)
    # Group-root -> (consensus prefix | None, distinct prefix count) from member
    # filenames; computed lazily by resolve_episode's mixed-folder guard.
    root_prefix_census: dict[Path, tuple[str | None, int]] = field(default_factory=dict)

    def get_season_json(self, tmdb: TmdbClient, tv_id: int, season: int) -> dict[str, Any]:
        key = (tv_id, season)
        if key not in self.season_cache:
            self.season_cache[key] = tmdb.tv_season(tv_id, season)
        return self.season_cache[key]


def _path_is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_under_extras_container(path: Path, entity_root: Path) -> bool:
    """True if any ancestor of ``path`` (strictly below ``entity_root``) is an
    extras-parent folder name (Featurettes / Deleted Scenes / …)."""
    cur = path.parent.resolve()
    stop = entity_root.resolve()
    while cur != stop and cur.name:
        if is_extras_parent_name(cur.name):
            return True
        nxt = cur.parent.resolve()
        if nxt == cur:
            break
        cur = nxt
    return False


def _format_episode_run(episodes: set[int]) -> str:
    """Compact contiguous-run formatter.

    ``{1,2,3,4,5,6,7,8,9,10,11,12,13}`` → ``"E1-E13"``;
    ``{1,2,3,4,5,7,8,9,10,11,12,13}``  → ``"E1-E5, E7-E13"``;
    ``{5}`` → ``"E5"``; ``set()`` → ``""``.
    """
    if not episodes:
        return ""
    nums = sorted(episodes)
    runs: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n != prev + 1:
            runs.append(f"E{start}" if start == prev else f"E{start}-E{prev}")
            start = n
        prev = n
    runs.append(f"E{start}" if start == prev else f"E{start}-E{prev}")
    return ", ".join(runs)


def _summarise_pack_seasons(
    member_files: list[Path],
) -> tuple[dict[int, set[int]], str]:
    """Group pack members by season → set of episode numbers and produce the
    short summary string that goes on the decision line.

    Single season → ``S03 (E1-E13)`` (gaps spelled out as ``E1-E5, E8-E13``).
    Multi-season → ``S01-S04 (52 eps)``. Extras-only pack → ``""``.
    """
    by_season: dict[int, set[int]] = {}
    for f in member_files:
        sxe = parse_sxe(f)
        if sxe is None:
            continue
        season, episode = sxe
        by_season.setdefault(season, set()).add(episode)
    if not by_season:
        return {}, ""
    seasons = sorted(by_season)
    if len(seasons) == 1:
        s = seasons[0]
        return by_season, f"S{s:02d} ({_format_episode_run(by_season[s])})"
    total = sum(len(eps) for eps in by_season.values())
    return by_season, f"S{seasons[0]:02d}-S{seasons[-1]:02d} ({total} eps)"


def _compute_missing(
    by_season: dict[int, set[int]],
    ctx: "PlanContext",
    tmdb: TmdbClient,
    tv_id: int,
) -> str:
    """Return missing-episodes string (e.g. ``"E13"`` or ``"S01E07, S03E02-S03E05"``)
    or ``""`` when the pack covers every expected episode.

    Skips season 0 (specials) — TMDB specials inventories are notoriously
    incomplete and would generate false positives. Also skips when the pack
    has a single episode in a single season; that's usually intentional
    (user grabbed one episode on purpose).
    """
    if not by_season:
        return ""
    only_one_season = len(by_season) == 1
    if only_one_season:
        sole = next(iter(by_season.values()))
        if len(sole) == 1:
            return ""
    parts: list[str] = []
    for season in sorted(by_season):
        if season == 0:
            continue
        try:
            payload = ctx.get_season_json(tmdb, tv_id, season)
        except Exception:
            continue
        expected = {
            ep.get("episode_number")
            for ep in (payload.get("episodes") or [])
            if isinstance(ep.get("episode_number"), int)
        }
        missing = expected - by_season[season]
        if not missing:
            continue
        # The function already returned "" for single-episode single-season
        # packs above; here we always emit if there are missing.
        if only_one_season:
            parts.append(_format_episode_run(missing))  # type: ignore[arg-type]
        else:
            for run in _format_episode_run(missing).split(", "):  # type: ignore[arg-type]
                if "-" in run:
                    lhs, rhs = run.split("-", 1)
                    parts.append(f"S{season:02d}{lhs}-S{season:02d}{rhs}")
                else:
                    parts.append(f"S{season:02d}{run}")
    return ", ".join(parts)


def _prefix_census(files: list[Path]) -> tuple[str | None, int]:
    """(consensus prefix, distinct prefix count) across episode-bearing members.

    52 files that all start ``STATIC SHOCK - Sxx Eyy`` are a stronger series
    signal than any folder name. Consensus requires at least two agreeing files
    covering ≥70% of the members with a usable prefix. Several disagreeing
    prefixes (a crossover collection) yield ``(None, N)`` so callers can treat
    the folder as *mixed* instead of stamping one show onto everything.
    """
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for f in files:
        if parse_sxe(f) is None:
            continue
        prefix = series_prefix_from_stem(f.stem)
        if not prefix:
            continue
        key = prefix.lower()
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, prefix)
    if not counts:
        return None, 0
    top = max(counts, key=lambda k: counts[k])
    if counts[top] >= 2 and counts[top] / sum(counts.values()) >= 0.7:
        return display[top], len(counts)
    return None, len(counts)


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def prepare_pack_tv_resolve(ctx: PlanContext, tmdb: TmdbClient, input_root: Path) -> None:
    """
    One TV series pick per **first-level folder** under ``--input`` when that subtree
    looks like a single show pack. Never uses ancestors above ``input_root``.
    """
    ir = input_root.resolve()
    ctx.input_root = ir
    for entity in entity_roots_under_input(ctx.all_files, ir):
        subset = [f for f in ctx.all_files if _path_is_within(entity, f)]
        if not is_single_tv_pack(subset, entity):
            continue
        cleaned = clean_stem_for_search(entity.name)
        # Title prefix before the first junk boundary ("STATIC SHOCK (2000-…" →
        # "STATIC SHOCK"); legacy subtractive cleaning only when the name
        # *starts* with junk and there is no prefix to extract.
        query = title_prefix(entity.name)
        if not query:
            # Enum-strip the RAW name before cleaning — clean_stem_for_search
            # collapses "a. Season 1" to "a" and the enumeration punctuation is
            # gone by the time the title comes back.
            legacy = clean_stem_for_search(strip_leading_enum(entity.name))
            query = (legacy.title or legacy.raw_stem or entity.name).strip()
            query = re.sub(
                r"(?i)\b(S\d{1,4}|Season\s*\d{1,4}|Complete(?:\s*Series)?)\b", " ", query
            )
            query = trim_stranded_separators(query)
        if not query:
            continue
        consensus, distinct = _prefix_census(subset)
        if consensus is None and distinct >= 2:
            # Member filenames name several different series (a crossover
            # collection shipped as one folder): binding a single show would
            # stamp it onto all of them. Resolve per-file instead.
            _user_notice(
                entity,
                f"Pack TV: member filenames name {distinct} different series; "
                "files will resolve individually (review in Phase 1.5).",
            )
            continue
        # Retry ladder: folder-derived query first (so a sane folder name keeps
        # driving the search), then the consensus series prefix across member
        # filenames, then the same candidates without the year filter. Later
        # rungs only run when the previous one returned nothing — except when
        # the folder name has nothing in common with what the files call the
        # show; then the consensus searches first, so an unrelated folder name
        # that happens to match *something* on TMDB can't win by accident.
        rungs: list[tuple[str, int | None, str]] = [(query, cleaned.year, "folder name")]
        if consensus and consensus.lower() != query.lower():
            rung = (consensus, cleaned.year, "filename consensus")
            if _name_similarity(consensus, query) < 0.5:
                rungs.insert(0, rung)
            else:
                rungs.append(rung)
        if cleaned.year is not None:
            rungs.extend((q, None, f"{src}, no year filter") for q, _y, src in list(rungs))
        results: list[dict[str, Any]] = []
        used_query, used_src, degraded = query, "folder name", False
        for i, (q, y, src) in enumerate(rungs):
            try:
                results = _dedupe_tv(tmdb.search_tv(q, y))
            except TmdbAuthError:
                raise
            except Exception:
                results = []
            if results:
                used_query, used_src, degraded = q, src, i > 0
                break
            if i + 1 < len(rungs):
                nq, ny, _ns = rungs[i + 1]
                yn = f" (year filter {y})" if y else ""
                nyn = f" (year filter {ny})" if ny else ""
                _user_notice(
                    entity,
                    f"Pack TV search: no results for {q!r}{yn}; retrying with {nq!r}{nyn}.",
                )
        y_note = f" (year filter {cleaned.year})" if cleaned.year else ""
        if not results:
            # Phase 1 is silent — defer to the Phase 1.5 search-review UI where
            # the user can drop into prompt_search_with_type via the edit action.
            _user_notice(
                entity,
                f"Pack TV search: no results for {query!r}{y_note}; "
                "files will resolve individually (review in Phase 1.5).",
            )
            continue
        pack_label = used_query
        picked = _auto_pick(
            results,
            pack_label.lower(),
            lambda m: (m.get("name") or m.get("original_name") or ""),
            filename_year=cleaned.year,
            extract_year=_year_from_tv_search_row,
        )
        if picked is None:
            continue
        pick, confidence, reason, candidates = picked
        if used_src != "folder name":
            # Surface non-folder query sources in Phase 1.5. Only a *fallback*
            # rung (something already failed first) caps confidence — a
            # front-loaded consensus is the strongest signal available, not a
            # degraded one.
            reason = f"{reason}; query from {used_src} {used_query!r}"
            if degraded and confidence == "high":
                confidence = "medium"
        tv_id = int(pick["id"])
        detail = tmdb.tv_detail(tv_id)
        series_name = detail.get("name") or detail.get("original_name") or "Series"
        first_air = detail.get("first_air_date") or ""
        tv_year_str = first_air[:4] if len(first_air) >= 4 and first_air[:4].isdigit() else ""
        tv_year = int(tv_year_str) if tv_year_str else None
        er = entity.resolve()
        ctx.entity_packs[er] = PackTvBinding(
            tmdb_tv_id=tv_id,
            series_name=series_name,
            year=tv_year,
            confidence=confidence,
            reason=reason,
            candidates=candidates,
        )
        ctx.series_by_root[er] = (tv_id, series_name)
        ctx.series_year_by_tv_id[tv_id] = tv_year
        by_season, summary = _summarise_pack_seasons(subset)
        missing = _compute_missing(by_season, ctx, tmdb, tv_id) if by_season else ""
        _entity_decision_notice(
            "TV",
            series_name,
            tv_year,
            tv_id,
            entity,
            summary=summary or None,
            missing=missing or None,
        )


_COLLECTION_HINT = re.compile(r"(?i)\b(collection|trilogy|anthology|saga|box\s*set|complete)\b")
_FOLDER_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


def _is_movie_collection_name(name: str) -> bool:
    """A folder is treated as a multi-movie collection (no entity binding) when its
    name says so ("COLLECTION", "Trilogy", …) or contains multiple distinct years."""
    if _COLLECTION_HINT.search(name):
        return True
    years = set(_FOLDER_YEAR.findall(name))
    return len(years) > 1


def prepare_movie_entity_resolve(ctx: PlanContext, tmdb: TmdbClient, input_root: Path) -> None:
    """
    One TMDB movie pick per top-level folder under ``--input`` when the folder name
    parses as ``Title (YYYY)`` / ``Title.YYYY.release-tail`` and the folder isn't
    already a TV pack. Runs **after** :func:`prepare_pack_tv_resolve` so that pack-TV
    bindings always win.
    """
    ir = input_root.resolve()
    ctx.input_root = ir
    for entity in entity_roots_under_input(ctx.all_files, ir):
        er = entity.resolve()
        if er in ctx.entity_packs:
            continue
        if not entity.is_dir():
            # Top-level loose files are resolved per-file by resolve_path; no binding.
            continue
        if _is_movie_collection_name(entity.name):
            _user_notice(
                entity,
                "Movie folder: looks like a collection (multiple years / 'COLLECTION' hint); "
                "files will resolve individually.",
            )
            continue
        cleaned = clean_stem_for_search(entity.name)
        title = (cleaned.title or "").strip()
        year = cleaned.year
        if not title or year is None:
            continue
        try:
            results = _dedupe_movies(tmdb.search_movie(title, year))
        except TmdbAuthError:
            raise
        except Exception:
            continue
        if not results:
            _user_notice(entity, f"Movie folder search: no TMDB hits for {title!r} ({year}); files will resolve individually.")
            continue
        picked = _auto_pick(
            results,
            title.lower(),
            lambda m: (m.get("title") or m.get("original_title") or ""),
            filename_year=year,
            extract_year=_year_from_movie_search_row,
        )
        if picked is None:
            continue
        pick, confidence, reason, candidates = picked
        mid = int(pick["id"])
        detail = tmdb.movie_detail(mid)
        full_title = detail.get("title") or detail.get("original_title") or title
        full_year = _year_from_movie(detail) or year
        ctx.entity_movies[er] = MovieEntityBinding(
            tmdb_movie_id=mid,
            title=full_title,
            year=full_year,
            confidence=confidence,
            reason=reason,
            candidates=candidates,
        )
        _entity_decision_notice("MOVIE", full_title, full_year, mid, entity)


def _bind_movie_entity_from_query(
    ctx: PlanContext,
    tmdb: TmdbClient,
    entity: Path,
    query: str,
    year: int | None,
) -> None:
    """Search TMDB for ``query`` and bind the result to the entity as a movie folder.

    Used when the pack-TV manual prompt is toggled to Movie — we want the user's
    typed text to drive the search and the chosen title to be logged immediately,
    not silently deferred to per-file resolution.
    """
    try:
        results = _dedupe_movies(tmdb.search_movie(query, year))
    except TmdbAuthError:
        raise
    except Exception as e:
        _user_notice(entity, f"Pack TV → movie search failed: {e}")
        return
    y_note = f" ({year})" if year else ""
    if not results:
        _user_notice(
            entity,
            f"Pack TV → movie search: no TMDB hits for {query!r}{y_note}; "
            "member files will resolve individually.",
        )
        return
    picked = _auto_pick(
        results,
        query.lower(),
        lambda m: (m.get("title") or m.get("original_title") or ""),
        filename_year=year,
        extract_year=_year_from_movie_search_row,
    )
    if picked is None:
        _user_notice(entity, "Pack TV → movie search cancelled; member files will resolve individually.")
        return
    pick, confidence, reason, candidates = picked
    mid = int(pick["id"])
    detail = tmdb.movie_detail(mid)
    full_title = detail.get("title") or detail.get("original_title") or query
    full_year = _year_from_movie(detail) or year
    ctx.entity_movies[entity.resolve()] = MovieEntityBinding(
        tmdb_movie_id=mid,
        title=full_title,
        year=full_year,
        confidence=confidence,
        reason=reason,
        candidates=candidates,
    )
    _entity_decision_notice("MOVIE", full_title, full_year, mid, entity)


def resolve_movie_entity_member(
    path: Path,
    output_root: Path,
    ctx: PlanContext,
    entity: Path,
) -> PlanEntry:
    """Resolve one file under a movie-bound entity folder."""
    bind = ctx.entity_movies.get(entity.resolve())
    if bind is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Movie entity context incomplete")

    # Plex movie local extras: non-main video inside Featurettes/, Deleted Scenes/, etc.
    if _is_under_extras_container(path, entity):
        extra_cat = infer_plex_extra_folder(path, entity_root=entity)
        ny = movie_name_with_year(bind.title, bind.year)
        movie_folder = sanitize_segment(ny + f" {{tmdb-{bind.tmdb_movie_id}}}")
        stem = sanitize_segment(strip_release_info(path.stem, aggressive=True) or path.stem)
        fname = sanitize_segment(stem + path.suffix)
        dest = output_root / "Movies" / movie_folder / extra_cat / fname
        return PlanEntry(
            src=path,
            dest=dest,
            kind="extra",
            tmdb_movie_id=bind.tmdb_movie_id,
            note=f"movie folder extra ({extra_cat})",
        )

    dest = build_movie_dest(
        output_root, bind.title, bind.year, path, tmdb_movie_id=bind.tmdb_movie_id
    )
    return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=bind.tmdb_movie_id)


def resolve_pack_tv_member(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
    entity: Path,
) -> PlanEntry:
    """Resolve one file under a per-input entity folder using that entity's pack TV identity."""
    packed = ctx.entity_packs.get(entity.resolve())
    if packed is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Pack context incomplete")
    tv_id, series_name = packed.tmdb_tv_id, packed.series_name

    # Files under an extras container (Featurettes/, Deleted Scenes/, …) are Plex
    # season extras — even if the filename contains SxxEyy. SxxEyy in an extras
    # file names the *episode the extra belongs to*, not "this is that episode".
    # This MUST run before the looks_episode check below; otherwise a Deleted
    # Scenes/S01E01 Scene 1.mkv falls through to resolve_episode, which then
    # re-searches TMDB for the parent folder name ("Deleted Scenes").
    if _is_under_extras_container(path, entity):
        season = infer_season_from_path_ancestors(path, entity)
        if season is None:
            sxe = parse_sxe(path)
            season = sxe[0] if sxe is not None else 0
        title = strip_release_info(path.stem, aggressive=True) or path.stem
        extra_cat = infer_plex_extra_folder(path, entity_root=entity)
        dest = build_season_extra_dest(
            output_root,
            series_name,
            season,
            path,
            tmdb_tv_id=tv_id,
            display_title=title,
            plex_extra_folder=extra_cat,
        )
        return PlanEntry(
            src=path,
            dest=dest,
            kind="extra",
            tmdb_tv_id=tv_id,
            season=season,
            episode=None,
            note=f"pack extra ({extra_cat})",
        )

    # Real episode under the bound show — finalize with the pack binding directly
    # to avoid the redundant TMDB show search that resolve_episode would do.
    if looks_episode(path) or guess_kind(path) == "episode":
        prefix = series_prefix_from_stem(path.stem)
        if prefix and _name_similarity(prefix, series_name) < 0.5:
            # The file names a different show than the pack binding (Static
            # Shock packs shipping JLU "TRUE Ending" episodes): stamping the
            # pack id would rename it into the wrong series — and collide with
            # the real owner of that SxxEyy slot. Resolve it as its own series.
            _user_notice(
                path,
                f"Pack member names a different series ({prefix!r} vs bound "
                f"{series_name!r}); resolving individually.",
            )
            return resolve_episode(path, output_root, tmdb, ctx)
        return _finalize_episode(path, output_root, tmdb, ctx, tv_id, series_name)

    season = infer_season_from_path_ancestors(path, entity)
    if season is not None:
        title = strip_release_info(path.stem, aggressive=True) or path.stem
        extra_cat = infer_plex_extra_folder(path, entity_root=entity)
        dest = build_season_extra_dest(
            output_root,
            series_name,
            season,
            path,
            tmdb_tv_id=tv_id,
            display_title=title,
            plex_extra_folder=extra_cat,
        )
        return PlanEntry(
            src=path,
            dest=dest,
            kind="extra",
            tmdb_tv_id=tv_id,
            season=season,
            episode=None,
            note=f"pack extra ({extra_cat})",
        )

    if looks_movie(path) and guess_kind(path) == "movie":
        return resolve_movie(path, output_root, tmdb, ctx)

    return resolve_ambiguous_dual(path, output_root, tmdb, ctx)


def _movie_label(m: dict[str, Any]) -> str:
    title = m.get("title") or m.get("original_title") or "?"
    rd = m.get("release_date") or ""
    y = rd[:4] if len(rd) >= 4 else ""
    mid = m.get("id", "")
    return f"{title} ({y}) {{tmdb-{mid}}}" if y else f"{title} {{tmdb-{mid}}}"


def _tv_label(m: dict[str, Any]) -> str:
    name = m.get("name") or m.get("original_name") or "?"
    fd = m.get("first_air_date") or ""
    y = fd[:4] if len(fd) >= 4 else ""
    tid = m.get("id", "")
    return f"{name} ({y}) {{tmdb-{tid}}}" if y else f"{name} {{tmdb-{tid}}}"


def _tmdb_overview(row: dict[str, Any]) -> str | None:
    """TMDB search row overview for questionary Choice.description (highlighted row only)."""
    ov = str(row.get("overview") or "").strip()
    if not ov:
        return None
    max_len = 380
    if len(ov) > max_len:
        return f"{ov[: max_len - 1]}…"
    return ov


def _year_from_movie(m: dict[str, Any]) -> int | None:
    rd = m.get("release_date") or ""
    if len(rd) >= 4 and rd[:4].isdigit():
        return int(rd[:4])
    return None


def _year_from_movie_search_row(m: dict[str, Any]) -> int | None:
    """Calendar year from a movie search result row."""
    return _year_from_movie(m)


def _year_from_tv_search_row(m: dict[str, Any]) -> int | None:
    """Calendar year from a TV search result row (`first_air_date`)."""
    fd = m.get("first_air_date") or ""
    if len(fd) >= 4 and fd[:4].isdigit():
        return int(fd[:4])
    return None


def _dedupe_movies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for m in rows:
        i = m.get("id")
        if not isinstance(i, int) or i in seen:
            continue
        seen.add(i)
        out.append(m)
    return out


def _dedupe_tv(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for m in rows:
        i = m.get("id")
        if not isinstance(i, int) or i in seen:
            continue
        seen.add(i)
        out.append(m)
    return out


def _imdb_tt(imdb_int: int) -> str:
    if imdb_int >= 10_000_000:
        return f"tt{imdb_int}"
    return f"tt{imdb_int:07d}"


def _user_notice(path: Path | None, message: str) -> None:
    """Non-interactive status line before prompts (stderr so it stays visible with questionary)."""
    tag = f" [{path.name}]" if path is not None else ""
    print(f"TitleForge{tag}: {message}", file=sys.stderr, flush=True)


_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_RED = "\033[31m"
_ANSI_KIND: dict[str, str] = {
    "MOVIE": "\033[1;92m",  # bright bold green
    "TV": "\033[1;96m",     # bright bold cyan
}


def _entity_decision_notice(
    kind: str,
    title: str,
    year: int | str | None,
    tmdb_id: int,
    entity: Path,
    *,
    summary: str | None = None,
    missing: str | None = None,
) -> None:
    """Single consolidated decision line per entity binding: ``[KIND] Title (Year) {tmdb-id}``.

    The entity ``entity`` is intentionally **not** printed — for confident binds
    the chosen TMDB title already names what was matched, and the source folder
    is surfaced separately in the Phase 1.5 search-review table. ANSI color is
    applied to ``[MOVIE]``/``[TV]`` so the kind scans at a glance; the colors
    are stripped when stderr isn't a TTY so piped/logged output stays clean.

    ``summary`` (e.g. ``S01 (E1-E13)``) renders in dim grey between the title
    and the TMDB id tag — useful when several packs of the same series bind
    one after another and would otherwise look identical. ``missing`` (e.g.
    ``E11, E12, E13``) prints on a second indented line in red, prefixed with
    ``⚠ missing:`` so incomplete rips are obvious at a glance.
    """
    y = f" ({year})" if year else ""
    is_tty = sys.stderr.isatty()
    if is_tty:
        tag = f"{_ANSI_KIND.get(kind, '')}[{kind}]{_ANSI_RESET}"
        idtag = f"{_ANSI_DIM}{{tmdb-{tmdb_id}}}{_ANSI_RESET}"
        s_text = f" {_ANSI_DIM}{summary}{_ANSI_RESET}" if summary else ""
    else:
        tag = f"[{kind}]"
        idtag = f"{{tmdb-{tmdb_id}}}"
        s_text = f" {summary}" if summary else ""
    print(f"{tag} {title}{y}{s_text} {idtag}", file=sys.stderr, flush=True)
    if missing:
        warn = (
            f"    {_ANSI_RED}⚠ missing: {missing}{_ANSI_RESET}"
            if is_tty
            else f"    ! missing: {missing}"
        )
        print(warn, file=sys.stderr, flush=True)


TPick = TypeVar("TPick")

# Silence the no-longer-used inline-picker kwargs from old callsites without
# renaming the function. Phase 1 is silent now — UI happens in search_review_app.
_UnusedPickerKw = Any


def _auto_pick(
    items: list[TPick],
    query: str,
    key_fn: Callable[[TPick], str],
    *,
    filename_year: int | None = None,
    extract_year: Callable[[TPick], int | None] | None = None,
    max_candidates: int = 15,
) -> tuple[TPick, ConfidenceLevel, str, list[TPick]] | None:
    """Silent auto-pick — never prompts.

    Returns ``(pick, confidence, reason, top_candidates)`` or ``None`` if the
    list is empty. The decision is one of:

    - **high**, "single TMDB hit" — one result.
    - **high**, "single year match (YYYY)" — exactly one result matches the
      filename's year hint.
    - **medium**, "similarity X.XX (Δ Y.YY)" — top similarity score ≥ 0.62 and
      beats runner-up by ≥ 0.07.
    - **low**, "ambiguous (N candidates, top X.XX)" — no decisive winner; the
      top result is returned anyway and the candidate list is preserved so
      the user can flip the pick in the search-review UI.
    """
    if not items:
        return None
    if len(items) == 1:
        return items[0], "high", "single TMDB hit", list(items)
    if (
        filename_year is not None
        and filename_year > 0
        and extract_year is not None
    ):
        matches = [it for it in items if extract_year(it) == filename_year]
        if len(matches) == 1:
            return (
                matches[0],
                "high",
                f"single year match ({filename_year})",
                list(items),
            )
    q = query.lower()
    scored = sorted(
        items,
        key=lambda it: difflib.SequenceMatcher(None, q, key_fn(it).lower()).ratio(),
        reverse=True,
    )
    best = scored[0]
    best_s = difflib.SequenceMatcher(None, q, key_fn(best).lower()).ratio()
    second_s = (
        difflib.SequenceMatcher(None, q, key_fn(scored[1]).lower()).ratio()
        if len(scored) > 1
        else 0.0
    )
    candidates = scored[:max_candidates]
    if best_s >= 0.62 and (best_s - second_s) >= 0.07:
        return (
            best,
            "medium",
            f"similarity {best_s:.2f} (Δ {best_s - second_s:.2f})",
            candidates,
        )
    return (
        best,
        "low",
        f"ambiguous ({len(scored)} candidates, top {best_s:.2f})",
        candidates,
    )


def _auto_pick_or_select(
    title: str,
    items: list[TPick],
    label: Callable[[TPick], str],
    query: str,
    key_fn: Callable[[TPick], str],
    *,
    header_path: Path | None = None,
    filename_year: int | None = None,
    extract_year: Callable[[TPick], int | None] | None = None,
    quiet: bool = False,
    # Accepted-but-ignored kwargs from the old interactive picker. Phase 1 is
    # silent; menus/styles only apply in the search-review UI now.
    select_message: _UnusedPickerKw = None,
    style: _UnusedPickerKw = None,
    use_indicator: _UnusedPickerKw = False,
    description: _UnusedPickerKw = None,
) -> TPick | None:
    """Backwards-compat shim wrapping :func:`_auto_pick`. Returns only the pick;
    confidence/reason are dropped at this boundary. New code should call
    :func:`_auto_pick` directly so the metadata reaches the search-review UI."""
    result = _auto_pick(
        items,
        query,
        key_fn,
        filename_year=filename_year,
        extract_year=extract_year,
    )
    if result is None:
        return None
    pick, conf, reason, _candidates = result
    if not quiet and header_path is not None:
        _user_notice(header_path, f"[{conf}] {reason}: {label(pick)}")
    return pick


TaggedHit = tuple[Literal["movie", "tv"], dict[str, Any]]


def _year_from_tagged_hit(hit: TaggedHit) -> int | None:
    kind, row = hit
    if kind == "movie":
        return _year_from_movie_search_row(row)
    return _year_from_tv_search_row(row)


def _dual_choice_label(hit: TaggedHit) -> str:
    kind, row = hit
    if kind == "movie":
        t = row.get("title") or row.get("original_title") or "?"
        rd = row.get("release_date") or ""
        y = rd[:4] if len(rd) >= 4 else ""
        mid = row.get("id", "")
        return f"[Movie] {t} ({y}) {{tmdb-{mid}}}" if y else f"[Movie] {t} {{tmdb-{mid}}}"
    t = row.get("name") or row.get("original_name") or "?"
    fd = row.get("first_air_date") or ""
    y = fd[:4] if len(fd) >= 4 else ""
    tid = row.get("id", "")
    return f"[TV] {t} ({y}) {{tmdb-{tid}}}" if y else f"[TV] {t} {{tmdb-{tid}}}"


def _dual_key_fn(hit: TaggedHit) -> str:
    kind, row = hit
    if kind == "movie":
        return str(row.get("title") or row.get("original_title") or "")
    return str(row.get("name") or row.get("original_name") or "")


def _gather_dual_candidates(
    tmdb: TmdbClient,
    cleaned: CleanedQuery,
    manual_query: str | None = None,
) -> list[TaggedHit]:
    q = (manual_query.strip() if manual_query else None) or cleaned.title or cleaned.raw_stem
    if not q.strip():
        return []
    movies_m = _dedupe_movies(tmdb.search_movie(q, cleaned.year))
    tv_m = _dedupe_tv(tmdb.search_tv(q, cleaned.year))
    return [("movie", m) for m in movies_m[:12]] + [("tv", t) for t in tv_m[:12]]


def resolve_ambiguous_dual(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    # A sibling .nfo with a movie id settles the movie-vs-TV question outright.
    nfo_entry = _movie_from_nfo_ids(path, output_root, tmdb, ctx)
    if nfo_entry is not None:
        return nfo_entry

    cleaned = clean_stem_for_search(path.stem)
    q0 = cleaned.title or cleaned.raw_stem or path.stem
    y_note = f" (year filter {cleaned.year})" if cleaned.year else ""
    candidates = _gather_dual_candidates(tmdb, cleaned)
    if not candidates:
        _user_notice(
            path,
            f"No TMDB movie or TV results for {q0!r}{y_note}; file marked for review in Phase 1.5.",
        )
        ctx.per_file_label[path] = _PerFileLabel(
            kind="skipped",
            tmdb_id=None,
            title=q0,
            year=cleaned.year,
            confidence="low",
            reason=f"no TMDB hits for {q0!r}{y_note}",
        )
        return PlanEntry(src=path, dest=None, kind="skipped", note="No TMDB results")

    query_key = (cleaned.title or cleaned.raw_stem).lower()
    picked = _auto_pick(
        candidates,
        query_key,
        _dual_key_fn,
        filename_year=cleaned.year,
        extract_year=_year_from_tagged_hit,
    )
    if picked is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="No dual candidates")
    pick, confidence, reason, top_candidates = picked

    kind, row = pick
    if kind == "movie":
        mid = int(row["id"])
        detail = tmdb.movie_detail(mid)
        title = detail.get("title") or detail.get("original_title") or "Unknown"
        y = _year_from_movie(detail)
        dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
        ctx.per_file_label[path] = _PerFileLabel(
            kind="movie",
            tmdb_id=mid,
            title=title,
            year=y,
            confidence=confidence,
            reason=reason,
            candidates=[r for _, r in top_candidates],
        )
        return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=mid)

    tv_id = int(row["id"])
    detail = tmdb.tv_detail(tv_id)
    series_name = detail.get("name") or detail.get("original_name") or "Series"
    ctx.series_year_by_tv_id[tv_id] = _year_from_tv_search_row(detail)
    root = series_group_root(path, ctx.all_files)
    if root is not None:
        ctx.series_by_root[root] = (tv_id, series_name)
    entry = _finalize_episode(
        path,
        output_root,
        tmdb,
        ctx,
        tv_id,
        series_name,
    )
    ctx.per_file_label[path] = _PerFileLabel(
        kind="tv",
        tmdb_id=tv_id,
        title=series_name,
        year=_year_from_tv_search_row(row),
        confidence=confidence,
        reason=reason,
        candidates=[r for _, r in top_candidates],
    )
    return entry


def _derive_episode_title_from_stem(stem: str) -> str | None:
    """Pull a likely episode title out of a scene-style filename.

    For ``Firefly (2002) - S01E12 - The Message (1080p BluRay x265 Silence)``
    this returns ``"The Message"``. Returns ``None`` if the stem doesn't have
    an ``SxxEyy`` marker or the segment after it is empty after stripping
    release noise.
    """
    m = _S00E00.search(stem)
    if m is None:
        return None
    after = stem[m.end():]
    after = strip_release_info(after, aggressive=True)
    after = after.strip(" \t-_.")
    after = re.sub(r"\s+", " ", after).strip()
    return after or None


def _finalize_episode(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
    tv_id: int,
    series_name: str,
) -> PlanEntry:
    sxe = parse_sxe(path)
    if sxe is None:
        # Phase 1 is silent — flag for review instead of prompting. The user
        # sees this row in the Phase 1.5 search-review with reason "missing
        # SxxEyy" and can edit / skip from there.
        ctx.per_file_label[path] = _PerFileLabel(
            kind="tv",
            tmdb_id=tv_id,
            title=series_name,
            year=ctx.series_year_by_tv_id.get(tv_id),
            confidence="low",
            reason="missing SxxEyy",
        )
        return PlanEntry(
            src=path,
            dest=None,
            kind="skipped",
            tmdb_tv_id=tv_id,
            note="missing SxxEyy",
        )

    season, episode = sxe

    try:
        season_json = ctx.get_season_json(tmdb, tv_id, season)
    except TmdbAuthError:
        raise
    except Exception as e:
        return PlanEntry(
            src=path,
            dest=None,
            kind="episode",
            tmdb_tv_id=tv_id,
            season=season,
            episode=episode,
            note=f"Season fetch failed: {e}",
        )

    ep_title: str | None = None
    for ep in season_json.get("episodes") or []:
        if int(ep.get("episode_number", -1)) == episode:
            ep_title = ep.get("name")
            break
    derived_from_filename = False
    if not ep_title:
        # No TMDB title for this episode — try to lift one out of the filename
        # (most scene/Plex-formatted releases include `... - SxxEyy - <title>`)
        # before falling back to "Episode". No prompt either way.
        derived = _derive_episode_title_from_stem(path.stem)
        if derived:
            ep_title = derived
            derived_from_filename = True
        else:
            ep_title = "Episode"
            derived_from_filename = True

    if derived_from_filename:
        # Make it discoverable in the Phase 1.5 review so the user can sanity-
        # check the auto-derived title without us interrupting Phase 1.
        existing = ctx.per_file_label.get(path)
        if existing is None or existing.confidence == "high":
            ctx.per_file_label[path] = _PerFileLabel(
                kind="tv",
                tmdb_id=tv_id,
                title=series_name,
                year=ctx.series_year_by_tv_id.get(tv_id),
                confidence="medium",
                reason=f"episode title S{season:02d}E{episode:02d} derived from filename",
            )

    dest = build_episode_dest(
        output_root,
        series_name,
        season,
        episode,
        ep_title,
        path,
        tmdb_tv_id=tv_id,
    )
    # Record a label so _build_entity_labels doesn't catch-all on file #2..#N
    # of a pack (file #1 does the TMDB search and writes the label; subsequent
    # files reuse series_by_root and would otherwise have no per_file_label,
    # falling to the catch-all that stamps the raw filename as title).
    # Only write if no higher-priority label is already present (medium = derived
    # title, low = missing SxxEyy — both set above).
    if path not in ctx.per_file_label:
        ctx.per_file_label[path] = _PerFileLabel(
            kind="tv",
            tmdb_id=tv_id,
            title=series_name,
            year=ctx.series_year_by_tv_id.get(tv_id),
            confidence="high",
            reason="series binding",
        )
    return PlanEntry(
        src=path,
        dest=dest,
        kind="episode",
        tmdb_tv_id=tv_id,
        season=season,
        episode=episode,
    )


def _manual_movie(
    query: str,
    year: int | None,
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    try:
        merged = _dedupe_movies(tmdb.search_movie(query, year))
    except TmdbAuthError:
        raise
    except Exception as e:
        return PlanEntry(src=path, dest=None, kind="skipped", note=f"Movie search error: {e}")
    yh = f" (year {year})" if year else ""
    if not merged:
        _user_notice(path, f"No TMDB movie results for {query!r}{yh}; skipping.")
        ctx.per_file_label[path] = _PerFileLabel(
            kind="skipped",
            tmdb_id=None,
            title=query,
            year=year,
            confidence="low",
            reason=f"no TMDB movie hits for {query!r}",
        )
        return PlanEntry(src=path, dest=None, kind="skipped", note="No movie results")
    picked = _auto_pick(
        merged,
        query.lower().strip(),
        lambda m: (m.get("title") or m.get("original_title") or ""),
        filename_year=year,
        extract_year=_year_from_movie_search_row,
    )
    if picked is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="No movie candidates")
    pick, confidence, reason, candidates = picked
    mid = int(pick["id"])
    detail = tmdb.movie_detail(mid)
    title = detail.get("title") or detail.get("original_title") or "Unknown"
    y = _year_from_movie(detail)
    dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
    ctx.per_file_label[path] = _PerFileLabel(
        kind="movie",
        tmdb_id=mid,
        title=title,
        year=y,
        confidence=confidence,
        reason=reason,
        candidates=candidates,
    )
    return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=mid)


def _manual_tv(
    query: str,
    year: int | None,
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    try:
        results = _dedupe_tv(tmdb.search_tv(query, year))
    except TmdbAuthError:
        raise
    except Exception as e:
        return PlanEntry(src=path, dest=None, kind="skipped", note=f"TV search error: {e}")
    yh = f" (year {year})" if year else ""
    if not results:
        _user_notice(path, f"No TMDB TV results for {query!r}{yh}; skipping.")
        ctx.per_file_label[path] = _PerFileLabel(
            kind="skipped",
            tmdb_id=None,
            title=query,
            year=year,
            confidence="low",
            reason=f"no TMDB TV hits for {query!r}",
        )
        return PlanEntry(src=path, dest=None, kind="skipped", note="No TV results")
    picked = _auto_pick(
        results,
        query.lower().strip(),
        lambda m: (m.get("name") or m.get("original_name") or ""),
        filename_year=year,
        extract_year=_year_from_tv_search_row,
    )
    if picked is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="No TV candidates")
    pick, confidence, reason, candidates = picked
    tv_id = int(pick["id"])
    detail = tmdb.tv_detail(tv_id)
    series_name = detail.get("name") or detail.get("original_name") or "Series"
    ctx.series_year_by_tv_id[tv_id] = _year_from_tv_search_row(detail)
    root = series_group_root(path, ctx.all_files)
    if root is not None:
        ctx.series_by_root[root] = (tv_id, series_name)
    ctx.per_file_label[path] = _PerFileLabel(
        kind="tv",
        tmdb_id=tv_id,
        title=series_name,
        year=_year_from_tv_search_row(pick),
        confidence=confidence,
        reason=reason,
        candidates=candidates,
    )
    return _finalize_episode(path, output_root, tmdb, ctx, tv_id, series_name)


def _manual_dual(
    query: str,
    year: int | None,
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    cleaned = CleanedQuery(title=query, year=year, raw_stem=query, stripped_year_note=None)
    try:
        candidates = _gather_dual_candidates(tmdb, cleaned, manual_query=query)
    except TmdbAuthError:
        raise
    yh = f" (year {year})" if year else ""
    if not candidates:
        _user_notice(path, f"No TMDB results for {query!r}{yh}; skipping.")
        ctx.per_file_label[path] = _PerFileLabel(
            kind="skipped",
            tmdb_id=None,
            title=query,
            year=year,
            confidence="low",
            reason=f"no TMDB hits for {query!r}",
        )
        return PlanEntry(src=path, dest=None, kind="skipped", note="No dual results")
    picked = _auto_pick(
        candidates,
        query.lower().strip(),
        _dual_key_fn,
        filename_year=year,
        extract_year=_year_from_tagged_hit,
    )
    if picked is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="No dual candidates")
    pick, confidence, reason, top_candidates = picked
    kind, row = pick
    if kind == "movie":
        mid = int(row["id"])
        detail = tmdb.movie_detail(mid)
        title = detail.get("title") or detail.get("original_title") or "Unknown"
        y = _year_from_movie(detail)
        dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
        ctx.per_file_label[path] = _PerFileLabel(
            kind="movie",
            tmdb_id=mid,
            title=title,
            year=y,
            confidence=confidence,
            reason=reason,
            candidates=[r for _, r in top_candidates],
        )
        return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=mid)
    tv_id = int(row["id"])
    detail = tmdb.tv_detail(tv_id)
    series_name = detail.get("name") or detail.get("original_name") or "Series"
    ctx.series_year_by_tv_id[tv_id] = _year_from_tv_search_row(detail)
    root = series_group_root(path, ctx.all_files)
    if root is not None:
        ctx.series_by_root[root] = (tv_id, series_name)
    entry = _finalize_episode(path, output_root, tmdb, ctx, tv_id, series_name)
    ctx.per_file_label[path] = _PerFileLabel(
        kind="tv",
        tmdb_id=tv_id,
        title=series_name,
        year=_year_from_tv_search_row(row),
        confidence=confidence,
        reason=reason,
        candidates=[r for _, r in top_candidates],
    )
    return entry


def _manual_dispatch(
    *,
    search_type: SearchType,
    query: str,
    year: int | None,
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    """Re-route the user-toggled search type from the Phase 1.5 edit modal."""
    if search_type == "movie":
        return _manual_movie(query, year, path, output_root, tmdb, ctx)
    if search_type == "tv":
        return _manual_tv(query, year, path, output_root, tmdb, ctx)
    return _manual_dual(query, year, path, output_root, tmdb, ctx)


def _movie_from_nfo_ids(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry | None:
    """Resolve a movie from a sibling ``.nfo``'s TMDB / IMDb id, skipping the
    search step entirely. Returns ``None`` when no usable id is found."""
    imdb_id, tmdb_movie_id, _tmdb_tv_id = collect_ids_near_video(path)
    if tmdb_movie_id:
        detail = tmdb.movie_detail(tmdb_movie_id)
        title = detail.get("title") or detail.get("original_title") or "Unknown"
        y = _year_from_movie(detail)
        dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=tmdb_movie_id)
        ctx.per_file_label[path] = _PerFileLabel(
            kind="movie",
            tmdb_id=tmdb_movie_id,
            title=title,
            year=y,
            confidence="high",
            reason="from NFO TMDB id",
        )
        return PlanEntry(
            src=path,
            dest=dest,
            kind="movie",
            tmdb_movie_id=tmdb_movie_id,
            note="from NFO TMDB id",
        )
    if imdb_id:
        found = tmdb.find_imdb_movie(imdb_id)
        if found:
            mid = int(found["id"])
            detail = tmdb.movie_detail(mid)
            title = detail.get("title") or detail.get("original_title") or "Unknown"
            y = _year_from_movie(detail)
            dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
            ctx.per_file_label[path] = _PerFileLabel(
                kind="movie",
                tmdb_id=mid,
                title=title,
                year=y,
                confidence="high",
                reason=f"from IMDb {_imdb_tt(imdb_id)}",
            )
            return PlanEntry(
                src=path,
                dest=dest,
                kind="movie",
                tmdb_movie_id=mid,
                note=f"from IMDb {_imdb_tt(imdb_id)}",
            )
    return None


def resolve_movie(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    nfo_entry = _movie_from_nfo_ids(path, output_root, tmdb, ctx)
    if nfo_entry is not None:
        return nfo_entry

    cleaned = clean_stem_for_search(path.stem)
    year_hint = cleaned.year
    terms: list[str] = []
    if cleaned.title:
        terms.append(cleaned.title)
    for t in basename_terms(path):
        if t and t not in terms:
            terms.append(t)
    pf = parent_folder_term(path)
    if pf and pf not in terms:
        terms.append(pf)
    if cleaned.raw_stem and cleaned.raw_stem not in terms:
        terms.insert(0, cleaned.raw_stem)

    merged: list[dict[str, Any]] = []
    for term in terms[:6]:
        try:
            merged.extend(tmdb.search_movie(term, year_hint))
        except TmdbAuthError:
            raise
        except Exception:
            continue
    merged = _dedupe_movies(merged)
    primary_name = cleaned.title or path.stem
    yh = f" (year hint {year_hint})" if year_hint else ""
    if not merged:
        _user_notice(path, f"No TMDB movie hits for {primary_name!r}{yh}; review in Phase 1.5.")
        ctx.per_file_label[path] = _PerFileLabel(
            kind="skipped",
            tmdb_id=None,
            title=primary_name,
            year=year_hint,
            confidence="low",
            reason=f"no TMDB hits for {primary_name!r}{yh}",
        )
        return PlanEntry(src=path, dest=None, kind="skipped", note="No movie results")

    # Title similarity for auto-pick: use cleaned title only. Joining raw stem +
    # extra terms (e.g. "(2001)" still in raw_stem) dilutes SequenceMatcher vs TMDB titles.
    similarity_q = re.sub(r"\s+", " ", (cleaned.title or primary_name).lower()).strip()
    picked = _auto_pick(
        merged,
        similarity_q,
        lambda m: (m.get("title") or m.get("original_title") or ""),
        filename_year=year_hint,
        extract_year=_year_from_movie_search_row,
    )
    if picked is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="No movie candidates")
    pick, confidence, reason, candidates = picked
    mid = int(pick["id"])
    detail = tmdb.movie_detail(mid)
    title = detail.get("title") or detail.get("original_title") or "Unknown"
    y = _year_from_movie(detail)
    dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
    ctx.per_file_label[path] = _PerFileLabel(
        kind="movie",
        tmdb_id=mid,
        title=title,
        year=y,
        confidence=confidence,
        reason=reason,
        candidates=candidates,
    )
    return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=mid)


def resolve_episode(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    root = series_group_root(path, ctx.all_files)
    resolved_tv: tuple[int, str] | None = None
    if root is not None and root in ctx.series_by_root:
        resolved_tv = ctx.series_by_root[root]

    if resolved_tv is None:
        query = series_query_string(path)
        consensus: str | None = None
        mixed_folder = False
        if root is not None:
            census = ctx.root_prefix_census.get(root)
            if census is None:
                members = [f for f in ctx.all_files if _path_is_within(root, f)]
                census = _prefix_census(members)
                ctx.root_prefix_census[root] = census
            consensus, distinct = census
            mixed_folder = consensus is None and distinct >= 2
            # Title prefix of the group folder ("Pantheon.S01.…" → "Pantheon");
            # legacy subtractive cleaning only when the folder name starts with
            # junk. Mirrors prepare_pack_tv_resolve so loose episodes get the
            # same cleanup pack-bound ones already get. Skipped entirely for
            # mixed folders (crossover collections): their name describes no
            # single show, so each file must search as itself.
            if not mixed_folder:
                qn = title_prefix(root.name)
                if not qn:
                    qn = strip_release_info(strip_leading_enum(root.name), aggressive=True)
                    qn = re.sub(
                        r"(?i)\b(S\d{1,4}|Season\s*\d{1,4}|Complete(?:\s*Series)?)\b",
                        " ",
                        qn,
                    )
                    qn = trim_stranded_separators(qn)
                if qn:
                    query = qn
        stem_cleaned = clean_stem_for_search(path.stem)
        # Retry ladder: the folder/primary query, then the series prefix from
        # this file's own name, then first " - " segments and bracket-inlined
        # variants of each. Later rungs only run on zero results, so a good
        # first query costs nothing extra.
        # Each rung is (candidate, per_file): per_file marks queries derived
        # from THIS file's name rather than the shared folder — a hit on one of
        # those says nothing about sibling files, so it must never be cached as
        # the folder's series identity below. Dedupe keeps the first occurrence,
        # so when folder and filename agree the rung stays folder-derived.
        stem_prefix = series_prefix_from_stem(path.stem)
        raw_rungs: list[tuple[str, bool]] = [(query, False)]
        if mixed_folder and stem_prefix:
            # Mixed folder: the file's own series name is the only real signal.
            raw_rungs = [(stem_prefix, True)]
        elif stem_prefix:
            raw_rungs.append((stem_prefix, True))
        if consensus and not mixed_folder and _name_similarity(consensus, query) < 0.5:
            # Folder name has nothing in common with what the member files call
            # the show ("TRUE Ending (2005)" holding JLU episodes): search the
            # consensus first so an unrelated folder name that happens to match
            # something on TMDB can't win by accident.
            raw_rungs.insert(0, (consensus, False))
        for cand, per_file in list(raw_rungs):
            seg = cand.split(" - ")[0].strip()
            if seg:
                raw_rungs.append((seg, per_file))
        for cand, per_file in list(raw_rungs):
            if re.search(r"[()\[\]{}]", cand):
                inlined = re.sub(r"\s+", " ", re.sub(r"[()\[\]{}]", " ", cand)).strip()
                if inlined:
                    raw_rungs.append((inlined, per_file))
        seen: set[str] = set()
        ladder: list[tuple[str, bool]] = []
        for cand, per_file in raw_rungs:
            key = cand.lower()
            if key and key not in seen:
                seen.add(key)
                ladder.append((cand, per_file))

        results: list[dict[str, Any]] = []
        used_query, used_per_file, degraded = ladder[0][0], False, False
        last_error: Exception | None = None
        for i, (cand, per_file) in enumerate(ladder):
            try:
                results = _dedupe_tv(tmdb.search_tv(cand))
            except TmdbAuthError:
                raise
            except Exception as e:
                last_error = e
                results = []
            if results:
                used_query, used_per_file, degraded = cand, per_file, i > 0
                break
            if i + 1 < len(ladder):
                _user_notice(
                    path,
                    f"No TMDB TV results for {cand!r}; retrying with {ladder[i + 1][0]!r}.",
                )
        if not results and last_error is not None:
            return PlanEntry(
                src=path, dest=None, kind="skipped", note=f"TV search error: {last_error}"
            )

        if not results:
            tried = (
                f" (also tried {', '.join(repr(c) for c, _pf in ladder[1:])})"
                if len(ladder) > 1
                else ""
            )
            _user_notice(
                path, f"No TMDB TV results for {ladder[0][0]!r}{tried}; review in Phase 1.5."
            )
            ctx.per_file_label[path] = _PerFileLabel(
                kind="skipped",
                tmdb_id=None,
                title=ladder[0][0],
                year=stem_cleaned.year,
                confidence="low",
                reason=f"no TMDB TV hits for {ladder[0][0]!r}{tried}",
            )
            return PlanEntry(src=path, dest=None, kind="skipped", note="No TV results")

        picked = _auto_pick(
            results,
            used_query.lower(),
            lambda m: (m.get("name") or m.get("original_name") or ""),
            filename_year=stem_cleaned.year,
            extract_year=_year_from_tv_search_row,
        )
        if picked is None:
            return PlanEntry(src=path, dest=None, kind="skipped", note="No TV candidates")
        pick, confidence, reason, candidates = picked
        if degraded:
            # A fallback rung matched, not the primary query — log the pick now
            # and cap confidence so it surfaces in the Phase 1.5 review.
            reason = f"{reason}; matched via retry query {used_query!r}"
            if confidence == "high":
                confidence = "medium"
            picked_name = pick.get("name") or pick.get("original_name") or "?"
            _user_notice(
                path,
                f"[{confidence}] matched {picked_name!r} via retry query {used_query!r}.",
            )

        tv_id = int(pick["id"])
        detail = tmdb.tv_detail(tv_id)
        series_name = detail.get("name") or detail.get("original_name") or "Series"
        ctx.series_year_by_tv_id[tv_id] = _year_from_tv_search_row(detail)
        resolved_tv = (tv_id, series_name)
        # Cache on the folder only when the winning query was folder-derived
        # and the folder is not mixed. A filename-rung match in a mixed folder
        # (crossover collections) would otherwise stamp file #1's show onto
        # every sibling.
        if root is not None and not used_per_file and not mixed_folder:
            ctx.series_by_root[root] = resolved_tv
        ctx.per_file_label[path] = _PerFileLabel(
            kind="tv",
            tmdb_id=tv_id,
            title=series_name,
            year=_year_from_tv_search_row(pick),
            confidence=confidence,
            reason=reason,
            candidates=candidates,
        )

    assert resolved_tv is not None
    tv_id, series_name = resolved_tv
    return _finalize_episode(path, output_root, tmdb, ctx, tv_id, series_name)


def resolve_path(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
    *,
    ignore_tmdb: bool = False,
) -> PlanEntry:
    if not ignore_tmdb:
        tagged = parse_tmdb_tag_from_path(path)
        if tagged is not None:
            tid, media = tagged
            note = "Path already contains a {tmdb-<id>} tag; use --ignore-tmdb to re-resolve."
            if media == "movie":
                return PlanEntry(
                    src=path,
                    dest=None,
                    kind="skipped",
                    tmdb_movie_id=tid,
                    note=note,
                )
            return PlanEntry(
                src=path,
                dest=None,
                kind="skipped",
                tmdb_tv_id=tid,
                note=note,
            )
    if ctx.input_root is not None:
        ent = input_entity_for_path(ctx.input_root, path)
        if ent in ctx.entity_packs and _path_is_within(ent, path):
            entry = resolve_pack_tv_member(path, output_root, tmdb, ctx, ent)
            entry.entity_key = ent.resolve()
            return entry
        if ent in ctx.entity_movies and _path_is_within(ent, path):
            entry = resolve_movie_entity_member(path, output_root, ctx, ent)
            entry.entity_key = ent.resolve()
            return entry
    g = guess_kind(path)
    if is_series_pack_folder(path, ctx.all_files) and g == "movie":
        g = "ambiguous"

    if g == "ambiguous":
        entry = resolve_ambiguous_dual(path, output_root, tmdb, ctx)
    elif g == "episode":
        entry = resolve_episode(path, output_root, tmdb, ctx)
    else:
        entry = resolve_movie(path, output_root, tmdb, ctx)
    entry.entity_key = path.resolve()
    return entry


def build_plan(
    files: list[Path],
    output_root: Path,
    tmdb: TmdbClient,
    *,
    ignore_tmdb: bool = False,
    input_root: Path | None = None,
) -> RenamePlan:
    ctx = PlanContext(
        all_files=list(files),
        input_root=input_root.resolve() if input_root is not None else None,
    )
    try:
        if ctx.input_root is not None:
            prepare_pack_tv_resolve(ctx, tmdb, ctx.input_root)
            prepare_movie_entity_resolve(ctx, tmdb, ctx.input_root)
    except TmdbAuthError:
        raise
    entries: list[PlanEntry] = []
    for p in sorted(files, key=lambda x: str(x).lower()):
        entries.append(resolve_path(p, output_root, tmdb, ctx, ignore_tmdb=ignore_tmdb))
    _flag_destination_conflicts(entries, ctx)
    labels = _build_entity_labels(entries, ctx)
    return RenamePlan(entries=entries, labels=labels)


_TMDB_TAG_IN_NAME = re.compile(r"\s*\{tmdb-\d+\}")


def _conflict_title_from_dest(dest: Path) -> str:
    """Series / movie title for a conflict label, from the destination's
    ``{tmdb-…}``-tagged folder."""
    for p in dest.parents:
        if "{tmdb-" in p.name:
            return _TMDB_TAG_IN_NAME.sub("", p.name).strip()
    return dest.stem


def _flag_destination_conflicts(entries: list[PlanEntry], ctx: PlanContext) -> None:
    """Surface destination collisions in Phase 1.5 instead of letting them hide
    inside entity groups until Phase 2's Proceed guard refuses the whole plan.

    Two kinds: several plan entries sharing one destination (the same episode
    ripped into a pack *and* a crossovers folder), and a destination that
    already exists on disk from an earlier run. Affected entries are pulled out
    of their entity grouping into their own rows with LOW confidence (low sorts
    first in the search-review table) and noticed on stderr at plan time.
    """
    by_dest: dict[Path, list[PlanEntry]] = {}
    for e in entries:
        if e.dest is None or e.kind == "skipped":
            continue
        by_dest.setdefault(e.dest.resolve(), []).append(e)
    for dest, group in sorted(by_dest.items()):
        srcs = {e.src.resolve() for e in group}
        dup = len(srcs) > 1
        exists = dest.exists()
        if exists and len(srcs) == 1:
            try:
                # A file already sitting at its own destination is not a conflict.
                exists = not dest.samefile(next(iter(srcs)))
            except OSError:
                pass
        if not dup and not exists:
            continue
        if dup:
            names = " + ".join(sorted(e.src.name for e in group))
            _user_notice(None, f"Duplicate destination ({len(group)} files): {dest} <- {names}")
        if exists:
            _user_notice(None, f"Destination already exists on disk: {dest}")
        for e in group:
            bits: list[str] = []
            if dup:
                rivals = sorted(o.src.name for o in group if o.src != e.src)
                bits.append(f"duplicate destination — also from {', '.join(rivals)}")
            if exists:
                bits.append("destination already exists on disk")
            msg = "; ".join(bits)
            e.note = msg if not e.note else f"{e.note}; {msg}"
            # Own row in the search-review table — otherwise a pack member's
            # collision stays invisible inside the entity's "54 files" row.
            e.entity_key = e.src.resolve()
            prior = ctx.per_file_label.get(e.src)
            kind: Literal["movie", "tv", "skipped"] = (
                "movie" if (e.tmdb_movie_id or e.kind == "movie") else "tv"
            )
            ctx.per_file_label[e.src] = _PerFileLabel(
                kind=kind,
                tmdb_id=e.tmdb_tv_id or e.tmdb_movie_id,
                title=prior.title if prior is not None else _conflict_title_from_dest(dest),
                year=prior.year if prior is not None else None,
                confidence="low",
                reason=msg if prior is None else f"{msg} ({prior.reason})",
                candidates=list(prior.candidates) if prior is not None else [],
            )


_CONFIDENCE_RANK: dict[ConfidenceLevel, int] = {"low": 0, "medium": 1, "high": 2}


def _build_entity_labels(
    entries: list[PlanEntry], ctx: PlanContext
) -> list[EntityLabel]:
    """Group PlanEntries into one label per entity (folder or loose file).

    Sort order: low-confidence rows first (most likely to need editing in the
    search-review UI), then medium, then high. Within a tier, alphabetical by
    display name.
    """
    by_key: dict[Path, list[PlanEntry]] = {}
    for e in entries:
        key = e.entity_key if e.entity_key is not None else e.src.resolve()
        by_key.setdefault(key, []).append(e)

    labels: list[EntityLabel] = []
    for key, group in by_key.items():
        if key in ctx.entity_packs:
            pb = ctx.entity_packs[key]
            labels.append(
                EntityLabel(
                    key=key,
                    display_name=key.name,
                    kind="tv",
                    tmdb_id=pb.tmdb_tv_id,
                    title=pb.series_name,
                    year=pb.year,
                    confidence=pb.confidence,
                    reason=pb.reason,
                    file_count=len(group),
                    candidates=list(pb.candidates),
                )
            )
            continue
        if key in ctx.entity_movies:
            mb = ctx.entity_movies[key]
            labels.append(
                EntityLabel(
                    key=key,
                    display_name=key.name,
                    kind="movie",
                    tmdb_id=mb.tmdb_movie_id,
                    title=mb.title,
                    year=mb.year,
                    confidence=mb.confidence,
                    reason=mb.reason,
                    file_count=len(group),
                    candidates=list(mb.candidates),
                )
            )
            continue
        # Per-file label (key == file path). per_file_label is keyed by the
        # raw path handed to the resolver, while entity_key is the resolved
        # path — these differ when the input path crosses a symlink (e.g.
        # /var/... vs /private/var/... on macOS), so fall back to the group's
        # source paths.
        pf = ctx.per_file_label.get(key)
        if pf is None:
            for e in group:
                pf = ctx.per_file_label.get(e.src)
                if pf is not None:
                    break
        if pf is not None:
            labels.append(
                EntityLabel(
                    key=key,
                    display_name=key.name,
                    kind=pf.kind,
                    tmdb_id=pf.tmdb_id,
                    title=pf.title,
                    year=pf.year,
                    confidence=pf.confidence,
                    reason=pf.reason,
                    file_count=len(group),
                    candidates=list(pf.candidates),
                )
            )
            continue
        # Skipped via {tmdb-id} tag, or some other path with no recorded label.
        e0 = group[0]
        labels.append(
            EntityLabel(
                key=key,
                display_name=key.name,
                kind="skipped",
                tmdb_id=e0.tmdb_movie_id or e0.tmdb_tv_id,
                title=key.name,
                year=None,
                confidence="high",
                reason=e0.note or "no resolution",
                file_count=len(group),
                candidates=[],
            )
        )

    labels.sort(key=lambda lb: (_CONFIDENCE_RANK[lb.confidence], lb.display_name.lower()))
    return labels
