"""Typed, validated app secrets via pydantic-settings.

`get_settings()` is lru_cached and MUST only be called inside functions, never at
module import — Modal injects the secret env vars at container start, and an
import-time read can cache stale `None`s for the whole container's life (this is
the class of bug that once left TMDB lookups silently disabled). Field names map
to their UPPER_SNAKE env vars automatically (gemini_api_key -> GEMINI_API_KEY).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # Optional so a missing key degrades that one integration gracefully (the
    # client factory returns None) rather than crashing the whole service.
    gemini_api_key: str | None = None
    notion_integration_token: str | None = None
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    google_places_api_key: str | None = None
    google_youtube_api_key: str | None = None
    tmdb_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
