from __future__ import annotations

import re
from pathlib import Path

_IMDB = re.compile(r"tt(\d{7,8})\b", re.I)
_TMDB_MOVIE = re.compile(r"themoviedb\.org/movie/(\d+)", re.I)
_TMDB_TV = re.compile(r"themoviedb\.org/tv/(\d+)", re.I)
_XML_IMDB = re.compile(r"<id>tt(\d{7,8})</id>", re.I)
_UNIQUE_TMDB = re.compile(
    r'<uniqueid[^>]*type="tmdb"[^>]*>(\d+)</uniqueid>',
    re.I,
)


def collect_ids_near_video(video: Path) -> tuple[int | None, int | None, int | None]:
    """
    Scan sibling NFO files for imdb id (returns as int without tt) or TMDB ids.
    Returns (imdb_id, tmdb_movie_id, tmdb_tv_id) — only one movie/tv id typically.
    """
    imdb: int | None = None
    tmdb_movie: int | None = None
    tmdb_tv: int | None = None
    dirs = {video.parent}
    for d in dirs:
        for nfo in d.glob("*.nfo"):
            try:
                text = nfo.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in _IMDB.finditer(text):
                imdb = int(m.group(1))
            for m in _XML_IMDB.finditer(text):
                imdb = int(m.group(1))
            for m in _TMDB_MOVIE.finditer(text):
                tmdb_movie = int(m.group(1))
            for m in _TMDB_TV.finditer(text):
                tmdb_tv = int(m.group(1))
            for m in _UNIQUE_TMDB.finditer(text):
                tmdb_movie = int(m.group(1))
    return imdb, tmdb_movie, tmdb_tv
