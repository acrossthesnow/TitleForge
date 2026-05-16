from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

import typer

from titleforge.cleanup import remove_empty_source_dirs
from titleforge.config import ensure_tmdb_credentials_interactive, get_tmdb_api_key, load_dotenv_sources
from titleforge.discover import discover_videos
from titleforge.resolve import build_plan
from titleforge.review_app import run_review
from titleforge.search_review_app import run_search_review
from titleforge.tmdb_client import TmdbClient
from titleforge.tmdb_errors import TmdbAuthError

app = typer.Typer(help="TitleForge — TMDB-backed renamer with Plex-style Movies/ and Series/ layout.")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(importlib.metadata.version("titleforge"))
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
    input_dir: Path = typer.Option(
        ...,
        "--input",
        "-i",
        exists=True,
        file_okay=False,
        readable=True,
        help="Root directory to scan recursively for videos.",
    ),
    output_dir: Path = typer.Option(
        ...,
        "--output",
        "-o",
        file_okay=False,
        help="Library root (Movies/ and Series/ created beneath it).",
    ),
    lang: str | None = typer.Option(
        None,
        "--lang",
        help="TMDB language (e.g. en-US). Defaults to en-US.",
    ),
    ignore_tmdb: bool = typer.Option(
        False,
        "--ignore-tmdb",
        help="Re-resolve with TMDB even when the source path already contains a {tmdb-<id>} folder tag.",
    ),
    auto_approve: bool = typer.Option(
        False,
        "--auto-approve",
        help="Skip the Phase 1.5 search-review UI (useful for scripted runs).",
    ),
    cleanup: bool | None = typer.Option(
        None,
        "--cleanup/--no-cleanup",
        help=(
            "After successful moves, remove source subdirectories that contain "
            "no real videos (sample/junk leftovers are ignored). If neither "
            "--cleanup nor --no-cleanup is given, you'll be prompted at the end."
        ),
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    load_dotenv_sources()
    ensure_tmdb_credentials_interactive()
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    key = get_tmdb_api_key()
    tmdb = TmdbClient(key, lang)
    try:
        files = discover_videos(input_dir)
        if not files:
            typer.secho("No video files found under input.", fg=typer.colors.YELLOW)
            raise typer.Exit(1)
        typer.echo(f"Resolving {len(files)} file(s) with TMDB…")
        try:
            plan = build_plan(
                files,
                output_dir,
                tmdb,
                ignore_tmdb=ignore_tmdb,
                input_root=input_dir,
            )
        except TmdbAuthError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None
        if not auto_approve:
            typer.echo("Opening search review (Phase 1.5)…")
            search_outcome = run_search_review(plan, output_dir, tmdb)
            if search_outcome != "proceed":
                typer.secho("Cancelled at search review — no files were moved.", fg=typer.colors.YELLOW)
                raise typer.Exit(1) from None
        typer.echo("Opening file-move review (Phase 2)…")
        outcome = run_review(plan, output_dir)
        if outcome == "proceed":
            typer.secho("Moves completed.", fg=typer.colors.GREEN)
            _maybe_cleanup_source(input_dir, cleanup)
        else:
            typer.secho("Cancelled — no files were moved.", fg=typer.colors.YELLOW)
    except KeyboardInterrupt:
        typer.secho("\nInterrupted — no files were moved.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(130) from None
    finally:
        tmdb.close()


def _maybe_cleanup_source(input_dir: Path, cleanup: bool | None) -> None:
    """Apply the --cleanup tri-state policy.

    - ``True`` / ``False``: act / skip without prompting.
    - ``None``: prompt the user; non-interactive runs default to skip so
      scripted invocations stay safe.
    """
    do_cleanup: bool
    if cleanup is True:
        do_cleanup = True
    elif cleanup is False:
        do_cleanup = False
    elif sys.stdin.isatty() and sys.stdout.isatty():
        do_cleanup = typer.confirm(
            "Remove empty source directories (no real videos remain)?",
            default=False,
        )
    else:
        # No flag, no TTY → safest is to leave the inbox alone.
        do_cleanup = False

    if not do_cleanup:
        return

    removed = remove_empty_source_dirs(input_dir)
    if not removed:
        typer.secho("Cleanup: no empty source directories to remove.", fg=typer.colors.CYAN)
        return
    typer.secho(f"Cleanup: removed {len(removed)} source director{'y' if len(removed) == 1 else 'ies'}:", fg=typer.colors.CYAN)
    for d in removed:
        typer.echo(f"  - {d}")


def run_cli() -> None:
    app()


if __name__ == "__main__":
    run_cli()
