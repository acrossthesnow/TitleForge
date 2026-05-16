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

from titleforge.classify import _S00E00, guess_kind, looks_episode, looks_movie, parse_sxe, series_query_string
from titleforge.extra_category import infer_plex_extra_folder
from titleforge.models import ConfidenceLevel, EntityLabel, PlanEntry, RenamePlan
from titleforge.nfo import collect_ids_near_video
from titleforge.normalize import basename_terms, parent_folder_term, strip_release_info
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
        query = (cleaned.title or cleaned.raw_stem or entity.name).strip()
        # Strip season / series-pack hints baked into the folder name so e.g.
        # `Firefly (2002) Season 1 S01 (1080p ...)` searches as `Firefly`.
        query = re.sub(r"(?i)\b(S\d{1,4}|Season\s*\d{1,4}|Complete(?:\s*Series)?)\b", " ", query)
        query = re.sub(r"\s+", " ", query).strip()
        if not query:
            continue
        try:
            results = tmdb.search_tv(query, cleaned.year)
        except TmdbAuthError:
            raise
        except Exception:
            continue
        results = _dedupe_tv(results)
        y_note = f" (year filter {cleaned.year})" if cleaned.year else ""
        pack_label = query
        if not results:
            # Phase 1 is silent — defer to the Phase 1.5 search-review UI where
            # the user can drop into prompt_search_with_type via the edit action.
            _user_notice(
                entity,
                f"Pack TV search: no results for {pack_label!r}{y_note}; "
                "files will resolve individually (review in Phase 1.5).",
            )
            continue
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
        _entity_decision_notice("TV", series_name, tv_year, tv_id, entity)


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
) -> None:
    """Single consolidated decision line per entity binding: ``[KIND] Title (Year) {tmdb-id}``.

    The entity ``entity`` is intentionally **not** printed — for confident binds
    the chosen TMDB title already names what was matched, and the source folder
    is surfaced separately in the Phase 1.5 search-review table. ANSI color is
    applied to ``[MOVIE]``/``[TV]`` so the kind scans at a glance; the colors
    are stripped when stderr isn't a TTY so piped/logged output stays clean.
    """
    y = f" ({year})" if year else ""
    if sys.stderr.isatty():
        tag = f"{_ANSI_KIND.get(kind, '')}[{kind}]{_ANSI_RESET}"
        idtag = f"{_ANSI_DIM}{{tmdb-{tmdb_id}}}{_ANSI_RESET}"
    else:
        tag = f"[{kind}]"
        idtag = f"{{tmdb-{tmdb_id}}}"
    print(f"{tag} {title}{y} {idtag}", file=sys.stderr, flush=True)


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
            year=None,
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
                year=None,
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


def resolve_movie(
    path: Path,
    output_root: Path,
    tmdb: TmdbClient,
    ctx: PlanContext,
) -> PlanEntry:
    imdb_id, tmdb_movie_id, _tmdb_tv_id = collect_ids_near_video(path)
    if tmdb_movie_id:
        detail = tmdb.movie_detail(tmdb_movie_id)
        title = detail.get("title") or detail.get("original_title") or "Unknown"
        y = _year_from_movie(detail)
        dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=tmdb_movie_id)
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
            return PlanEntry(
                src=path,
                dest=dest,
                kind="movie",
                tmdb_movie_id=mid,
                note=f"from IMDb {_imdb_tt(imdb_id)}",
            )

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
        if root is not None:
            qn = strip_release_info(root.name, aggressive=True)
            qn = re.sub(r"\s+", " ", qn).strip()
            if qn:
                query = qn
        stem_cleaned = clean_stem_for_search(path.stem)
        try:
            results = tmdb.search_tv(query)
        except TmdbAuthError:
            raise
        except Exception as e:
            return PlanEntry(src=path, dest=None, kind="skipped", note=f"TV search error: {e}")

        results = _dedupe_tv(results)
        if not results:
            _user_notice(path, f"No TMDB TV results for {query!r}; review in Phase 1.5.")
            ctx.per_file_label[path] = _PerFileLabel(
                kind="skipped",
                tmdb_id=None,
                title=query,
                year=stem_cleaned.year,
                confidence="low",
                reason=f"no TMDB TV hits for {query!r}",
            )
            return PlanEntry(src=path, dest=None, kind="skipped", note="No TV results")

        picked = _auto_pick(
            results,
            query.lower(),
            lambda m: (m.get("name") or m.get("original_name") or ""),
            filename_year=stem_cleaned.year,
            extract_year=_year_from_tv_search_row,
        )
        if picked is None:
            return PlanEntry(src=path, dest=None, kind="skipped", note="No TV candidates")
        pick, confidence, reason, candidates = picked

        tv_id = int(pick["id"])
        detail = tmdb.tv_detail(tv_id)
        series_name = detail.get("name") or detail.get("original_name") or "Series"
        resolved_tv = (tv_id, series_name)
        if root is not None:
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
    labels = _build_entity_labels(entries, ctx)
    return RenamePlan(entries=entries, labels=labels)


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
        # Per-file label (key == file path).
        pf = ctx.per_file_label.get(key)
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
