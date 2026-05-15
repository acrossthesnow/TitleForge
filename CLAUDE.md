# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Install for development (Python ≥3.11, src layout):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # dev extras add `build` + `twine`
```

Run the CLI (console entry `titleforge` → `titleforge.cli:run_cli`):

```bash
titleforge --input /path/to/inbox --output /path/to/library
titleforge --ignore-tmdb -i ... -o ...   # force re-resolve even when a {tmdb-<id>} tag is already on the path
```

Tests are plain `unittest` (no pytest dep):

```bash
python -m unittest discover -s tests          # full suite
python -m unittest tests.test_pack_series     # single module
python -m unittest tests.test_pack_series.TestPackHeuristics.test_content_root  # single test
```

Build artifacts / smoke-test a wheel (see README "For maintainers"):

```bash
python -m build && twine check dist/*
```

Releases are tag-driven: pushing `v*` triggers `.github/workflows/release.yml`, which builds wheel + sdist and attaches them to a GitHub Release. There is no PyPI publish step.

Ruff is configured in `pyproject.toml` (`line-length = 100`, `target-version = "py311"`) but is not wired into CI — run `ruff check src tests` manually if needed.

## Architecture

TitleForge is a two-phase TMDB-backed renamer that produces Plex-style `Movies/<Title (Year) {tmdb-<id>}>/...` and `Series/<Show {tmdb-<id>}>/Season NN/...` library paths.

**Phase 1 (resolve)** — `cli.py` → `discover.discover_videos` → `resolve.build_plan`. Each input video produces a `PlanEntry(src, dest, kind, ...)`. TMDB is hit lazily; auth failures raise `TmdbAuthError` (HTTP 401/403 in `tmdb_client.TmdbClient._request`) which the CLI surfaces and exits on.

**Phase 2 (review)** — `review_app.run_review` opens a Textual TUI with a scrollable plan and `[P]roceed / [C]ancel / [M]odify` bindings. Only Proceed performs filesystem moves.

### Module map (functional grouping)

- `cli.py` / `__main__.py` — Typer entry point; loads dotenv config, ensures TMDB creds, builds plan, runs review.
- `config.py` — `titleforge.conf` resolution: user config dir (XDG / `%APPDATA%`) then `cwd/titleforge.conf` (override). Interactive first-run prompt writes the key with 0600 perms. Non-TTY runs without creds raise `SystemExit`.
- `tmdb_client.py` — Thin httpx client for `/search/movie`, `/search/tv`, `/movie/{id}`, `/tv/{id}`, `/tv/{id}/season/{n}`, `/find/{imdb_id}`. **Dual auth**: values that look like v4 JWTs (`eyJ…` with two dots, len > 80) go via `Authorization: Bearer …`; everything else is sent as the v3 `api_key=` query parameter. Do **not** swap these — JWT in `api_key=` returns 401.
- `discover.py` — Recursive video scan. Extensions mirror FileBot `MediaTypes`. Skips `*sample*` files and `*.txt`/`*.md` junk.
- `classify.py` — Filename heuristics: `SxxEyy`, `NxNN`, `Season N Episode N`, and `Title (YYYY)` movie pattern. Episode signals win over movie signals (FileBot precedence).
- `query_clean.py` / `normalize.py` — Strip release tags, codecs, scene group suffixes, and produce a `CleanedQuery(title, year, raw_stem, …)` used for TMDB search and similarity scoring.
- `pack.py` / `series_folder.py` — TV "pack" detection. A folder is a single-show pack if its immediate children are season folders (`Season N` / `Sn`) and/or extras-container folders (`Featurettes`, `Extras`, …). `content_root` walks up to find the deepest pack root, bounded by a ceiling (always `--input` — never above).
- `extra_category.py` — Maps extras-container folder names to Plex local-extras subfolders (`Featurettes`, `Behind The Scenes`, `Deleted Scenes`, `Other`, …).
- `plex_paths.py` — All sanitization + path-building. Mirrors FileBot's `PlexNamingStandard`: illegal chars stripped, smart quotes folded, colons → ` - `, invisible/BOM chars removed, titles truncated at 150 chars. Functions: `build_movie_dest`, `build_episode_dest`, `build_season_extra_dest`, and `parse_tmdb_tag_from_path` (used by the skip rule).
- `resolve.py` — Orchestrator. Holds `PlanContext` with `series_by_root`, `entity_packs`, and a `season_cache` keyed by `(tv_id, season)` so one season is fetched once. Dispatch order in `resolve_path`:
  1. If path already contains `{tmdb-<id>}` (or legacy `[tmdb-<id>]`) under `Movies/` or `Series/` → skip, unless `--ignore-tmdb`.
  2. If the file lives under a pre-bound input entity (`entity_packs`) → `resolve_pack_tv_member`.
  3. Else by `guess_kind` → `resolve_episode`, `resolve_movie`, or `resolve_ambiguous_dual` (movie + TV search merged).
- `nfo.py` — Reads sibling `.nfo` files for IMDb / TMDB ids so movies with embedded ids skip the search step.
- `review_app.py` — Textual UI. DataTable cells take Rich markup, so paths containing `{tmdb-…}` / brackets must be rendered as literal `Text` to avoid markup parsing.
- `prompt_ui.py` — `questionary` styling + TTY-clear helpers for interactive disambiguation.

### Cross-cutting invariants

- **Pack binding is per top-level input folder.** `prepare_pack_tv_resolve` iterates `entity_roots_under_input` (first-level dirs under `--input`) and, for each one that passes `is_single_tv_pack`, asks the user to pick the show **once**. Every member file under that entity then uses the bound `(tmdb_tv_id, series_name)`. Never walk above `--input` when binding — see the `ceiling` argument on `content_root`.
- **Auto-pick vs. menu.** `_auto_pick_or_select` will auto-pick TMDB results in two cases: exactly one result, or exactly one result whose year matches the filename year. Otherwise it scores candidates with `difflib.SequenceMatcher` against the cleaned title and only auto-picks if the top score ≥ 0.62 **and** beats the runner-up by ≥ 0.07. Tests in `test_resolve_similarity.py` lock the similarity-query construction (use `cleaned.title` lowercased, not the raw stem with year still attached).
- **TMDB id tag format.** Primary movie/series folder names get a brace-form suffix ` {tmdb-<id>}`; episode/extra filenames inside do **not** repeat it. Legacy `[tmdb-<id>]` (bracket form) is still recognized by `parse_tmdb_tag_from_path` for skip detection only — never produced by the writer.
- **Specials.** Season 0 always renders as `Specials/` (folder) regardless of source layout; see `build_episode_dest` and `build_season_extra_dest`.
- **Path sanitization happens at the segment level.** Always route names through `plex_paths.sanitize_segment` before joining — it handles colon replacement, illegal-char stripping, multi-space collapse, trailing-punct/dot trim, and zero-width/BOM removal that bare `str.strip()` misses.
