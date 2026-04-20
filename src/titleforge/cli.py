from __future__ import annotations

import importlib.metadata
from pathlib import Path

import typer

from titleforge.config import ensure_tmdb_credentials_interactive, get_tmdb_api_key, load_dotenv_sources
from titleforge.discover import discover_videos
from titleforge.resolve import build_plan
from titleforge.review_app import run_review
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
            plan = build_plan(files, output_dir, tmdb, ignore_tmdb=ignore_tmdb)
        except TmdbAuthError as e:
            typer.secho(str(e), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None
        typer.echo("Opening review…")
        outcome = run_review(plan, output_dir)
        if outcome == "proceed":
            typer.secho("Moves completed.", fg=typer.colors.GREEN)
        else:
            typer.secho("Cancelled — no files were moved.", fg=typer.colors.YELLOW)
    except KeyboardInterrupt:
        typer.secho("\nInterrupted — no files were moved.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(130) from None
    finally:
        tmdb.close()


def run_cli() -> None:
    app()


if __name__ == "__main__":
    run_cli()
