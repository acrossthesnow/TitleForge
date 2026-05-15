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

from titleforge.classify import guess_kind, looks_episode, looks_movie, parse_sxe, series_query_string
from titleforge.extra_category import infer_plex_extra_folder
from titleforge.models import PlanEntry, RenamePlan
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
    parse_tmdb_tag_from_path,
)
from titleforge.prompt_ui import LIST_STYLE, SearchType, clear_tty, prompt_search_with_type
from titleforge.query_clean import CleanedQuery, clean_stem_for_search
from titleforge.series_folder import is_series_pack_folder, series_group_root
from titleforge.tmdb_client import TmdbClient
from titleforge.tmdb_errors import TmdbAuthError


@dataclass
class PlanContext:
    all_files: list[Path]
    series_by_root: dict[Path, tuple[int, str]] = field(default_factory=dict)
    season_cache: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    # Resolved ``--input``; pack TV binds only per first-level folder beneath it.
    input_root: Path | None = None
    # Top-level entity dir -> (tmdb_tv_id, series_name) from one pack pick per folder.
    entity_packs: dict[Path, tuple[int, str]] = field(default_factory=dict)

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
            _user_notice(
                entity,
                f"Pack TV search: no results for folder name {query!r}{y_note}; enter a show title below.",
            )
            res = prompt_search_with_type(
                "Pack TV search: enter show title:",
                default=query,
                initial_type="tv",
            )
            if res is None:
                continue
            pack_search_type, pack_label = res
            if pack_search_type != "tv":
                _user_notice(
                    entity,
                    f"Pack TV search: type switched to {pack_search_type!r}; skipping pack binding "
                    "for this folder — member files will resolve individually.",
                )
                continue
            try:
                results = _dedupe_tv(tmdb.search_tv(pack_label, cleaned.year))
            except TmdbAuthError:
                raise
            except Exception:
                continue
        if not results:
            _user_notice(
                entity,
                f"Pack TV search: still no results for {pack_label!r}{y_note}; skipping pack for this folder.",
            )
            continue
        pick = _auto_pick_or_select(
            "Select TV series (pack)",
            results,
            _tv_label,
            pack_label.lower(),
            lambda m: (m.get("name") or m.get("original_name") or ""),
            header_path=entity,
            style=LIST_STYLE,
            use_indicator=True,
            description=_tmdb_overview,
            filename_year=cleaned.year,
            extract_year=_year_from_tv_search_row,
        )
        if pick is None:
            continue
        tv_id = int(pick["id"])
        detail = tmdb.tv_detail(tv_id)
        series_name = detail.get("name") or detail.get("original_name") or "Series"
        er = entity.resolve()
        ctx.entity_packs[er] = (tv_id, series_name)
        ctx.series_by_root[er] = (tv_id, series_name)


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
    tv_id, series_name = packed

    if looks_episode(path) or guess_kind(path) == "episode":
        return resolve_episode(path, output_root, tmdb, ctx)

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


TPick = TypeVar("TPick")


def _auto_pick_or_select(
    title: str,
    items: list[TPick],
    label: Callable[[TPick], str],
    query: str,
    key_fn: Callable[[TPick], str],
    *,
    header_path: Path | None = None,
    select_message: str | None = None,
    style: Style | None = None,
    use_indicator: bool = False,
    description: Callable[[TPick], str | None] | None = None,
    filename_year: int | None = None,
    extract_year: Callable[[TPick], int | None] | None = None,
) -> TPick | None:
    if not items:
        return None
    if len(items) == 1:
        _user_notice(header_path, f"Only one TMDB match — using: {label(items[0])}")
        return items[0]
    if (
        filename_year is not None
        and filename_year > 0
        and extract_year is not None
    ):
        matches = [it for it in items if extract_year(it) == filename_year]
        if len(matches) == 1:
            _user_notice(
                header_path,
                f"Single TMDB match for file year {filename_year} — using: {label(matches[0])}",
            )
            return matches[0]
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
    if best_s >= 0.62 and (best_s - second_s) >= 0.07:
        _user_notice(
            header_path,
            f"Auto-selected best TMDB title match (score {best_s:.2f}): {label(best)}",
        )
        return best
    _user_notice(
        header_path,
        f"TMDB returned {len(scored)} close candidate(s); choose in the menu ({title}).",
    )
    body = select_message or title
    if header_path is not None:
        clear_tty()
        # Plain text only — raw ANSI in this string is shown literally by prompt_toolkit.
        prompt = f"{header_path.stem}\n{body}"
    else:
        prompt = body
    sel_kw: dict[str, Any] = {}
    if style is not None:
        sel_kw["style"] = style
    if use_indicator:
        sel_kw["use_indicator"] = True
    if description is not None:
        sel_kw["show_description"] = True
    choices: list[questionary.Choice] = []
    for it in scored[:15]:
        if description is not None:
            desc = description(it)
            if desc:
                choices.append(questionary.Choice(label(it), it, description=desc))
            else:
                choices.append(questionary.Choice(label(it), it))
        else:
            choices.append(questionary.Choice(label(it), it))
    choice = questionary.select(prompt, choices=choices, **sel_kw).unsafe_ask(patch_stdout=True)
    return choice


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
    search_label = q0
    candidates = _gather_dual_candidates(tmdb, cleaned)
    if not candidates:
        _user_notice(
            path,
            f"No TMDB movie or TV results for {q0!r}{y_note}; enter a different search title below.",
        )
        res = prompt_search_with_type(
            "No TMDB movie or TV hits. Enter search title:",
            default=cleaned.title or cleaned.raw_stem,
            initial_type="both",
        )
        if res is None:
            return PlanEntry(src=path, dest=None, kind="skipped", note="No dual-search query")
        dual_type, search_label = res
        if dual_type != "both":
            return _manual_dispatch(
                search_type=dual_type,
                query=search_label,
                year=cleaned.year,
                path=path,
                output_root=output_root,
                tmdb=tmdb,
                ctx=ctx,
            )
        candidates = _gather_dual_candidates(
            tmdb,
            CleanedQuery(title=search_label, year=cleaned.year, raw_stem=cleaned.raw_stem, stripped_year_note=cleaned.stripped_year_note),
            manual_query=search_label,
        )
    if not candidates:
        _user_notice(
            path,
            f"Still no TMDB results for {search_label!r}{y_note}; skipping this file.",
        )
        return PlanEntry(src=path, dest=None, kind="skipped", note="No TMDB results")

    nm = sum(1 for k, _ in candidates if k == "movie")
    nt = sum(1 for k, _ in candidates if k == "tv")
    _user_notice(
        path,
        f"TMDB dual search: {nm} movie(s), {nt} TV show(s) for {search_label!r}{y_note}.",
    )

    query_key = (cleaned.title or cleaned.raw_stem).lower()
    pick = _auto_pick_or_select(
        "Select movie or TV match",
        candidates,
        _dual_choice_label,
        query_key,
        _dual_key_fn,
        header_path=path,
        style=LIST_STYLE,
        use_indicator=True,
        description=lambda hit: _tmdb_overview(hit[1]),
        filename_year=cleaned.year,
        extract_year=_year_from_tagged_hit,
    )
    if pick is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Cancelled dual pick")

    kind, row = pick
    if kind == "movie":
        mid = int(row["id"])
        detail = tmdb.movie_detail(mid)
        title = detail.get("title") or detail.get("original_title") or "Unknown"
        y = _year_from_movie(detail)
        dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
        return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=mid)

    tv_id = int(row["id"])
    detail = tmdb.tv_detail(tv_id)
    series_name = detail.get("name") or detail.get("original_name") or "Series"
    root = series_group_root(path, ctx.all_files)
    if root is not None:
        ctx.series_by_root[root] = (tv_id, series_name)
    return _finalize_episode(
        path,
        output_root,
        tmdb,
        ctx,
        tv_id,
        series_name,
    )


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
        q = questionary.text(
            "Could not parse SxxEyy. Enter season,episode as S,E (e.g. 3,12):",
            default="",
        ).unsafe_ask()
        if not q or "," not in q:
            return PlanEntry(src=path, dest=None, kind="skipped", note="No S/E")
        a, b = q.split(",", 1)
        try:
            sxe = (int(a.strip()), int(b.strip()))
        except ValueError:
            return PlanEntry(src=path, dest=None, kind="skipped", note="Bad S/E")

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
    if ep_title is None:
        ep_title = questionary.text(
            f"Episode title not on TMDB (S{season:02d}E{episode:02d}). Enter title or leave blank:",
            default="",
        ).unsafe_ask()
        if not ep_title:
            ep_title = "Episode"

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
        return PlanEntry(src=path, dest=None, kind="skipped", note="No movie results")
    pick = _auto_pick_or_select(
        "Select movie",
        merged,
        _movie_label,
        query.lower().strip(),
        lambda m: (m.get("title") or m.get("original_title") or ""),
        header_path=path,
        style=LIST_STYLE,
        use_indicator=True,
        description=_tmdb_overview,
        filename_year=year,
        extract_year=_year_from_movie_search_row,
    )
    if pick is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Cancelled movie pick")
    mid = int(pick["id"])
    detail = tmdb.movie_detail(mid)
    title = detail.get("title") or detail.get("original_title") or "Unknown"
    y = _year_from_movie(detail)
    dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
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
        return PlanEntry(src=path, dest=None, kind="skipped", note="No TV results")
    pick = _auto_pick_or_select(
        "Select TV series",
        results,
        _tv_label,
        query.lower().strip(),
        lambda m: (m.get("name") or m.get("original_name") or ""),
        header_path=path,
        style=LIST_STYLE,
        use_indicator=True,
        description=_tmdb_overview,
        filename_year=year,
        extract_year=_year_from_tv_search_row,
    )
    if pick is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Cancelled series pick")
    tv_id = int(pick["id"])
    detail = tmdb.tv_detail(tv_id)
    series_name = detail.get("name") or detail.get("original_name") or "Series"
    root = series_group_root(path, ctx.all_files)
    if root is not None:
        ctx.series_by_root[root] = (tv_id, series_name)
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
        return PlanEntry(src=path, dest=None, kind="skipped", note="No dual results")
    pick = _auto_pick_or_select(
        "Select movie or TV match",
        candidates,
        _dual_choice_label,
        query.lower().strip(),
        _dual_key_fn,
        header_path=path,
        style=LIST_STYLE,
        use_indicator=True,
        description=lambda hit: _tmdb_overview(hit[1]),
        filename_year=year,
        extract_year=_year_from_tagged_hit,
    )
    if pick is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Cancelled dual pick")
    kind, row = pick
    if kind == "movie":
        mid = int(row["id"])
        detail = tmdb.movie_detail(mid)
        title = detail.get("title") or detail.get("original_title") or "Unknown"
        y = _year_from_movie(detail)
        dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
        return PlanEntry(src=path, dest=dest, kind="movie", tmdb_movie_id=mid)
    tv_id = int(row["id"])
    detail = tmdb.tv_detail(tv_id)
    series_name = detail.get("name") or detail.get("original_name") or "Series"
    root = series_group_root(path, ctx.all_files)
    if root is not None:
        ctx.series_by_root[root] = (tv_id, series_name)
    return _finalize_episode(path, output_root, tmdb, ctx, tv_id, series_name)


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
    """Re-route a manual no-results prompt to the user-toggled search type."""
    if search_type == "movie":
        return _manual_movie(query, year, path, output_root, tmdb)
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
        _user_notice(
            path,
            f"No TMDB movie results from automated title queries{yh}; enter a manual search below.",
        )
        res = prompt_search_with_type(
            "No TMDB movie hits. Enter search query:",
            default=primary_name,
            initial_type="movie",
        )
        if res is None:
            return PlanEntry(src=path, dest=None, kind="skipped", note="No query")
        movie_type, manual_query = res
        if movie_type != "movie":
            return _manual_dispatch(
                search_type=movie_type,
                query=manual_query,
                year=year_hint,
                path=path,
                output_root=output_root,
                tmdb=tmdb,
                ctx=ctx,
            )
        merged = _dedupe_movies(tmdb.search_movie(manual_query, year_hint))
        if not merged:
            _user_notice(path, f"No TMDB movie results for manual query {manual_query!r}{yh}; skipping.")
            return PlanEntry(src=path, dest=None, kind="skipped", note="No movie results")

    # Title similarity for auto-pick / menu order: use cleaned title only. Joining raw stem +
    # extra terms (e.g. "(2001)" still in raw_stem) dilutes SequenceMatcher vs TMDB titles.
    similarity_q = re.sub(r"\s+", " ", (cleaned.title or primary_name).lower()).strip()
    pick = _auto_pick_or_select(
        "Select movie",
        merged,
        _movie_label,
        similarity_q,
        lambda m: (m.get("title") or m.get("original_title") or ""),
        header_path=path,
        style=LIST_STYLE,
        use_indicator=True,
        description=_tmdb_overview,
        filename_year=year_hint,
        extract_year=_year_from_movie_search_row,
    )
    if pick is None:
        return PlanEntry(src=path, dest=None, kind="skipped", note="Cancelled movie pick")
    mid = int(pick["id"])
    detail = tmdb.movie_detail(mid)
    title = detail.get("title") or detail.get("original_title") or "Unknown"
    y = _year_from_movie(detail)
    dest = build_movie_dest(output_root, title, y, path, tmdb_movie_id=mid)
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
            _user_notice(
                path,
                f"No TMDB TV results for show query {query!r}; enter a different title below.",
            )
            res = prompt_search_with_type(
                "No TMDB TV hits. Enter show search:",
                default=query,
                initial_type="tv",
            )
            if res is None:
                return PlanEntry(src=path, dest=None, kind="skipped", note="No show query")
            ep_type, ep_query = res
            if ep_type != "tv":
                return _manual_dispatch(
                    search_type=ep_type,
                    query=ep_query,
                    year=stem_cleaned.year,
                    path=path,
                    output_root=output_root,
                    tmdb=tmdb,
                    ctx=ctx,
                )
            query = ep_query
            results = _dedupe_tv(tmdb.search_tv(query))
        if not results:
            _user_notice(
                path,
                f"Still no TMDB TV results for {query!r}; skipping this file.",
            )
            return PlanEntry(src=path, dest=None, kind="skipped", note="No TV results")

        pick = _auto_pick_or_select(
            "Select TV series",
            results,
            _tv_label,
            query.lower(),
            lambda m: (m.get("name") or m.get("original_name") or ""),
            header_path=path,
            style=LIST_STYLE,
            use_indicator=True,
            description=_tmdb_overview,
            filename_year=stem_cleaned.year,
            extract_year=_year_from_tv_search_row,
        )
        if pick is None:
            return PlanEntry(src=path, dest=None, kind="skipped", note="Cancelled series pick")

        tv_id = int(pick["id"])
        detail = tmdb.tv_detail(tv_id)
        series_name = detail.get("name") or detail.get("original_name") or "Series"
        resolved_tv = (tv_id, series_name)
        if root is not None:
            ctx.series_by_root[root] = resolved_tv

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
            return resolve_pack_tv_member(path, output_root, tmdb, ctx, ent)
    g = guess_kind(path)
    if is_series_pack_folder(path, ctx.all_files) and g == "movie":
        g = "ambiguous"

    if g == "ambiguous":
        return resolve_ambiguous_dual(path, output_root, tmdb, ctx)
    if g == "episode":
        return resolve_episode(path, output_root, tmdb, ctx)
    return resolve_movie(path, output_root, tmdb, ctx)


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
    except TmdbAuthError:
        raise
    entries: list[PlanEntry] = []
    for p in sorted(files, key=lambda x: str(x).lower()):
        entries.append(resolve_path(p, output_root, tmdb, ctx, ignore_tmdb=ignore_tmdb))
    return RenamePlan(entries=entries)
