"""Typed, validated app secrets via pydantic-settings.

`get_settings()` is lru_cached and MUST only be called inside functions, never at
module import — Modal injects the secret env vars at container start, and an
import-time read can cache stale `None`s for the whole container's life (this is
the class of bug that once left TMDB lookups silently disabled). Field names map
to their UPPER_SNAKE env vars automatically (ai_api_key -> AI_API_KEY).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    # Credentials are read when the integration runs, never during module import.
    ai_api_key: str | None = None
    ai_base_url: str = "https://api.groq.com/openai/v1"
    ai_model: str = "qwen/qwen3.8-27b"
    ai_fallback_model: str | None = None
    ai_reasoning_effort: str = "low"
    spotify_client_id: str | None = None
    spotify_client_secret: str | None = None
    google_youtube_api_key: str | None = None
    tmdb_api_key: str | None = None
    # A user's Soma hub is a workspace credential (core/workspace.py), not an
    # app setting.


@lru_cache
def get_settings() -> Settings:
    return Settings()
