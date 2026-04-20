# TitleForge

Resolve movie and TV file paths with TMDB, then review moves in a terminal UI before applying.

## Install

### From source

Clone the repository, create a virtual environment, and install in editable mode:

```bash
git clone https://github.com/acrossthesnow/titleforge.git
cd titleforge
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

If you already have a local checkout, `cd` into the repo root and run the `venv` / `activate` / `pip` lines from there.

For wheel/sdist builds and `twine`, use **`pip install -e ".[dev]"`** once (see **For maintainers** below).

### Configuration

Create `.env` in the repo root with either:

- **`TMDB_API_KEY`** — the short **API Key (v3)** from [TMDB Settings → API](https://www.themoviedb.org/settings/api), or  
- **`TMDB_READ_ACCESS_TOKEN`** — the long **API Read Access Token** (JWT). You can also put that JWT in `TMDB_API_KEY`; the client detects JWTs (`eyJ…`) and sends `Authorization: Bearer …` instead of using `api_key=` (which would return **401**).

If you see **401 Unauthorized**, the credential is wrong for how it is sent: use the v3 key as `TMDB_API_KEY`, or the v4 JWT with Bearer (automatic when the value looks like a JWT).

## Usage

```bash
titleforge --input /path/to/inbox --output /path/to/library
# or: titleforge -i /path/to/inbox -o /path/to/library
```

### Behavior notes

Files already under `Movies/` or `Series/` with a `{tmdb-<id>}` tag on the primary folder in the path are **skipped** in phase 1 (no TMDB calls). Pass **`--ignore-tmdb`** to force a full re-resolve for every file.

Phase 1 resolves every video; Phase 2 shows a scrollable plan (source → destination). Use **Proceed** to move files, **Cancel** to exit without changes, **Modify** to edit the destination for the selected row. A trailing `(YYYY)` on the file stem is parsed as the release year for TMDB search; when several movies match, disambiguation ranks candidates against the **cleaned title** (without that parenthetical), not the raw stem.

Episodic content is placed under `Series/`; movies under `Movies/`. The **primary** movie or series folder name includes `{tmdb-<id>}` (movie filenames inside that folder do not repeat the id). Legacy `[tmdb-<id>]` paths are still recognized for skip detection.

## For maintainers

End users only need **Install** (including **Configuration**) and **Usage** above.

### Releases (GitHub)

Push a version tag (`v0.1.0`, …). [`.github/workflows/release.yml`](.github/workflows/release.yml) builds the wheel and sdist and attaches them to a **GitHub Release** for that tag (`pip install` from PyPI is separate; users can `pip install ./titleforge-*.whl` from the release assets).

### Building and verifying locally

Install build tools (once): `pip install -e ".[dev]"` from the repo root. Then:

```bash
python -m build
twine check dist/*
```

Smoke-test the wheel in a **fresh** venv: `python -m venv /tmp/tf-smoke && /tmp/tf-smoke/bin/pip install dist/titleforge-*.whl && /tmp/tf-smoke/bin/titleforge --help`

Installed package version: `titleforge --version` (matches `pyproject.toml` / `titleforge.__version__`).

### PyPI (optional)

Register the **`titleforge`** name on PyPI, create an API token or [trusted publisher](https://docs.pypi.org/trusted-publishers/), then from a clean tree with `dist/` built: `twine upload dist/*`. Update **`[project.urls]`** in `pyproject.toml` to your real repo before the first upload if placeholders are still in place.
