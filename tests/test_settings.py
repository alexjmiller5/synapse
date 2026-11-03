"""Tests for the pydantic-settings config surface."""

from core.settings import get_settings


def test_reads_env_vars():
    # conftest seeds fake secrets into the environment before import
    s = get_settings()
    assert s.gemini_api_key == "fake-gemini-key"
    assert s.tmdb_api_key is None  # not seeded -> graceful None, not a crash


def test_get_settings_is_cached():
    assert get_settings() is get_settings()


def test_cloudflare_credentials_are_typed_settings(monkeypatch):
    from core.settings import Settings

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    settings = Settings()
    assert settings.cloudflare_api_token == "test-token"
    assert settings.cloudflare_account_id == "test-account"
