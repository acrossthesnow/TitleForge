"""TMDB HTTP errors with user-facing messages (no secrets in strings)."""


class TmdbAuthError(Exception):
    """401/403 from TMDB — wrong or missing credentials."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
