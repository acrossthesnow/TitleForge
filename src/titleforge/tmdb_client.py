from __future__ import annotations

from typing import Any

import httpx

from titleforge.tmdb_errors import TmdbAuthError

TMDB_BASE = "https://api.themoviedb.org/3"

_AUTH_HELP = (
    "TMDB rejected the credentials (HTTP 401/403).\n"
    "  • Use the short **API Key (v3)** from https://www.themoviedb.org/settings/api as TMDB_API_KEY, or\n"
    "  • Use the long **API Read Access Token** (JWT) as TMDB_READ_ACCESS_TOKEN (or TMDB_API_KEY — "
    "the app sends JWTs as Authorization: Bearer, not as api_key=).\n"
    "Do not paste the JWT into the v3 api_key field on the TMDB website; only in this app's .env."
)


def _is_tmdb_read_access_token(value: str) -> bool:
    s = value.strip()
    if not s.startswith("eyJ") or s.count(".") != 2:
        return False
    return len(s) > 80


class TmdbClient:
    """
    TMDB API v3. Supports v3 **api_key** query param or v4 **Bearer** JWT on all requests.
    """

    def __init__(self, credential: str, language: str | None = None) -> None:
        self.language = (language or "en-US").strip()
        self._api_key: str | None = None
        headers: dict[str, str] = {}
        if _is_tmdb_read_access_token(credential):
            headers["Authorization"] = f"Bearer {credential.strip()}"
        else:
            self._api_key = credential.strip()
        self._client = httpx.Client(
            base_url=TMDB_BASE,
            timeout=60.0,
            headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def _params(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        p: dict[str, Any] = {"language": self.language}
        if self._api_key is not None:
            p["api_key"] = self._api_key
        if extra:
            p.update(extra)
        return p

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            r = self._client.request(method, url, **kwargs)
        except httpx.RequestError as e:
            raise RuntimeError(f"TMDB network error: {e}") from e
        if r.status_code in (401, 403):
            raise TmdbAuthError(_AUTH_HELP)
        return r

    def _get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        r = self._request("GET", url, **kwargs)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            return {}
        return data

    def find_imdb_movie(self, imdb_id: int) -> dict[str, Any] | None:
        tt = f"tt{imdb_id}" if imdb_id >= 10_000_000 else f"tt{imdb_id:07d}"
        data = self._get_json(
            f"/find/{tt}",
            params=self._params({"external_source": "imdb_id"}),
        )
        results = data.get("movie_results") or []
        return results[0] if results else None

    def movie_detail(self, movie_id: int) -> dict[str, Any]:
        return self._get_json(f"/movie/{movie_id}", params=self._params())

    def search_movie(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query}
        if year and year > 0:
            params["year"] = year
        data = self._get_json("/search/movie", params=self._params(params))
        return list(data.get("results") or [])

    def search_tv(self, query: str, first_air_year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query}
        if first_air_year and first_air_year > 0:
            params["first_air_date_year"] = first_air_year
        data = self._get_json("/search/tv", params=self._params(params))
        return list(data.get("results") or [])

    def tv_detail(self, tv_id: int) -> dict[str, Any]:
        return self._get_json(f"/tv/{tv_id}", params=self._params())

    def tv_season(self, tv_id: int, season_number: int) -> dict[str, Any]:
        return self._get_json(f"/tv/{tv_id}/season/{season_number}", params=self._params())
