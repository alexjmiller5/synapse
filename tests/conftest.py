"""
Shared test fixtures for the Synapse test suite.

Mocking strategy:
- Fake secrets are seeded as env vars BEFORE core modules import
  (core.secrets reads env vars; DB ids fall back to databases.yaml, so the
  fake NOTION_*_DB_ID vars here act as overrides that keep tests off real ids)
- core.clients module globals (notion, gemini_client, spotify, youtube, gmaps)
  are patched at the module level
- All external API calls are intercepted before any real network I/O
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fake secrets — seeded into the environment before core modules import
# ---------------------------------------------------------------------------
FAKE_SECRETS = {
    "gemini-api-key": "fake-gemini-key",
    "notion-integration-token": "fake-notion-token",
    "spotify-client-id": "fake-spotify-id",
    "spotify-client-secret": "fake-spotify-secret",
    "google-places-api-key": "fake-places-key",
    "google-youtube-api-key": "fake-youtube-key",
    # life-data hub (movies/tv-shows)
    "life-hub-url": "https://hub.test.invalid",
    "life-hub-token": "fake-hub-token",
    # DB IDs
    "notion-tasks-db-id": "fake-tasks-db-id",
    "notion-groceries-db-id": "fake-groceries-db-id",
    "notion-ideas-db-id": "fake-ideas-db-id",
    "notion-quotes-db-id": "fake-quotes-db-id",
    "notion-podcasts-db-id": "fake-podcasts-db-id",
    "notion-youtube-videos-db-id": "fake-yt-videos-db-id",
    "notion-youtube-channels-db-id": "fake-yt-channels-db-id",
    "notion-fun-activities-db-id": "fake-fun-db-id",
    "notion-people-db-id": "fake-people-db-id",
    "notion-bookmarks-db-id": "fake-bookmarks-db-id",
    "notion-bucket-list-db-id": "fake-bucket-list-db-id",
    "notion-places-db-id": "fake-places-db-id",
    "notion-logs-db-id": "fake-logs-db-id",
    "notion-trips-db-id": "fake-trips-db-id",
    "notion-projects-db-id": "fake-projects-db-id",
    "notion-notes-db-id": "fake-notes-db-id",
}

for _sid, _val in FAKE_SECRETS.items():
    os.environ.setdefault(_sid.upper().replace("-", "_"), _val)

# Patch external client constructors BEFORE core.clients is imported
# googlemaps.Client validates key format at __init__, so we must intercept it
patch("google.genai.Client", return_value=MagicMock()).start()
patch("notion_client.Client", return_value=MagicMock()).start()
patch("spotipy.Spotify", return_value=MagicMock()).start()
patch("googlemaps.Client", return_value=MagicMock()).start()
patch("googleapiclient.discovery.build", return_value=MagicMock()).start()

# Import the clients module — the external constructors are patched above, so the
# lazy getters build MOCK clients. Each getter is lru_cached, so calling it here
# returns the same instance the code-under-test will get.
import core.clients as _clients_mod  # noqa: E402

_mock_notion = _clients_mod.get_notion()
_mock_notion.pages.create.return_value = {
    "id": "new-page-id-000",
    "url": "https://www.notion.so/New-Page-newpageid000",
}
_mock_notion.pages.update.return_value = {
    "id": "updated-page-id",
    "url": "https://www.notion.so/Updated-Page-updatedpageid",
}
_mock_notion.request.return_value = {"results": []}
_mock_notion.databases.retrieve.return_value = {"properties": {}}

_mock_gemini = _clients_mod.get_gemini_client()
_mock_spotify = _clients_mod.get_spotify()
_mock_youtube = _clients_mod.get_youtube()
_mock_gmaps = _clients_mod.get_gmaps()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_all_mocks():
    """Auto-reset all mocks before every test to prevent state bleed."""
    _mock_notion.reset_mock()
    _mock_notion.pages.create.return_value = {
        "id": "new-page-id-000",
        "url": "https://www.notion.so/New-Page-newpageid000",
    }
    _mock_notion.pages.update.return_value = {
        "id": "updated-page-id",
        "url": "https://www.notion.so/Updated-Page-updatedpageid",
    }
    _mock_notion.request.return_value = {"results": []}
    _mock_notion.request.side_effect = None
    _mock_notion.databases.retrieve.return_value = {"properties": {}}
    _mock_notion.pages.create.side_effect = None
    _mock_notion.pages.update.side_effect = None

    _mock_gemini.reset_mock()
    _mock_gemini.models.generate_content.side_effect = None
    _mock_spotify.reset_mock()
    _mock_youtube.reset_mock()
    _mock_gmaps.reset_mock()
    yield


@pytest.fixture
def mock_notion():
    """Provides the mock Notion client."""
    return _mock_notion


@pytest.fixture
def mock_gemini():
    """Provides a mock Gemini client that returns configurable JSON."""
    return _mock_gemini


def make_gemini_response(json_data):
    """Helper to create a mock Gemini response with .text property."""
    resp = MagicMock()
    resp.text = json.dumps(json_data)
    resp.candidates = [MagicMock(finish_reason="STOP", safety_ratings=[])]
    return resp


@pytest.fixture
def mock_spotify():
    """Provides the mock Spotify client."""
    _mock_spotify.reset_mock()
    return _mock_spotify


@pytest.fixture
def mock_youtube():
    """Provides the mock YouTube client."""
    _mock_youtube.reset_mock()
    return _mock_youtube


@pytest.fixture
def mock_gmaps():
    """Provides the mock Google Maps client."""
    _mock_gmaps.reset_mock()
    return _mock_gmaps
