"""
Shared test fixtures for the Synapse processor test suite.

Mocking strategy:
- gcp_secrets.get_secret is patched to return fake DB IDs / API keys
- clients module globals (notion, gemini_client, spotify, youtube, gmaps)
  are patched at the module level
- All external API calls are intercepted before any real network I/O
"""

import json
import os
import sys
import base64
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the processor package is importable
# ---------------------------------------------------------------------------
PROCESSOR_DIR = os.path.join(os.path.dirname(__file__), "..")
TESTS_DIR = os.path.dirname(__file__)
if PROCESSOR_DIR not in sys.path:
    sys.path.insert(0, PROCESSOR_DIR)
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

# ---------------------------------------------------------------------------
# Fake secrets — returned by the patched get_secret
# ---------------------------------------------------------------------------
FAKE_SECRETS = {
    "gemini-api-key": "fake-gemini-key",
    "notion-integration-token": "fake-notion-token",
    "spotify-client-id": "fake-spotify-id",
    "spotify-client-secret": "fake-spotify-secret",
    "google-places-api-key": "fake-places-key",
    "google-youtube-api-key": "fake-youtube-key",
    # DB IDs
    "notion-tasks-db-id": "fake-tasks-db-id",
    "notion-groceries-db-id": "fake-groceries-db-id",
    "notion-ideas-db-id": "fake-ideas-db-id",
    "notion-quotes-db-id": "fake-quotes-db-id",
    "notion-movies-db-id": "fake-movies-db-id",
    "notion-tv-shows-db-id": "fake-tv-shows-db-id",
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


def _fake_get_secret(secret_id, version="latest"):
    return FAKE_SECRETS.get(secret_id)


# ---------------------------------------------------------------------------
# Patch gcp_secrets BEFORE importing any processor modules
# ---------------------------------------------------------------------------
# The SecretManagerServiceClient() constructor hangs when there are no
# GCP credentials (tries to reach metadata server). We must mock the
# entire google.cloud.secretmanager module BEFORE gcp_secrets is imported.
_mock_sm_module = MagicMock()
sys.modules["google.cloud.secretmanager"] = _mock_sm_module
sys.modules["google.cloud"] = sys.modules.get("google.cloud", MagicMock())
# functions_framework (imported by main.py, used in test_pipeline_e2e / test_integration)
# does `from google.cloud.functions.context import Context`. Register these submodules
# explicitly so the import resolves deterministically even when google.cloud is a MagicMock.
sys.modules["google.cloud.functions"] = MagicMock()
sys.modules["google.cloud.functions.context"] = MagicMock()

# Now import gcp_secrets — it will get our mock secretmanager
import gcp_secrets

# Override everything in gcp_secrets to use our fakes
gcp_secrets.get_secret = _fake_get_secret
gcp_secrets.sm_client = MagicMock()
gcp_secrets.SECRETS = dict(FAKE_SECRETS)
gcp_secrets.DATABASE_IDS = {k.replace("notion-", "").replace("-db-id", ""): v
                            for k, v in FAKE_SECRETS.items()
                            if k.startswith("notion-") and k.endswith("-db-id")}

# Patch external client constructors BEFORE clients.py is imported
# googlemaps.Client validates key format at __init__, so we must intercept it
patch("google.genai.Client", return_value=MagicMock()).start()
patch("notion_client.Client", return_value=MagicMock()).start()
patch("spotipy.Spotify", return_value=MagicMock()).start()
patch("googlemaps.Client", return_value=MagicMock()).start()
patch("googleapiclient.discovery.build", return_value=MagicMock()).start()

# Now import clients module — constructors are mocked so no real API calls happen
import clients as _clients_mod
_clients_mod.GEMINI_API_KEY = "fake-gemini-key"
_clients_mod.NOTION_API_KEY = "fake-notion-token"

# Create mock clients and assign them to the module globals
_mock_notion = MagicMock()
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

_clients_mod.notion = _mock_notion
_clients_mod.gemini_client = MagicMock()
_clients_mod.spotify = MagicMock()
_clients_mod.youtube = MagicMock()
_clients_mod.gmaps = MagicMock()

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

    _clients_mod.gemini_client.reset_mock()
    _clients_mod.spotify.reset_mock()
    _clients_mod.youtube.reset_mock()
    _clients_mod.gmaps.reset_mock()
    yield


@pytest.fixture
def mock_notion():
    """Provides the mock Notion client."""
    return _mock_notion


@pytest.fixture
def mock_gemini():
    """Provides a mock Gemini client that returns configurable JSON."""
    return _clients_mod.gemini_client


def make_gemini_response(json_data):
    """Helper to create a mock Gemini response with .text property."""
    resp = MagicMock()
    resp.text = json.dumps(json_data)
    resp.candidates = [MagicMock(finish_reason="STOP", safety_ratings=[])]
    return resp


@pytest.fixture
def mock_spotify():
    """Provides the mock Spotify client."""
    mock = _clients_mod.spotify
    mock.reset_mock()
    return mock


@pytest.fixture
def mock_youtube():
    """Provides the mock YouTube client."""
    mock = _clients_mod.youtube
    mock.reset_mock()
    return mock


@pytest.fixture
def mock_gmaps():
    """Provides the mock Google Maps client."""
    mock = _clients_mod.gmaps
    mock.reset_mock()
    return mock


def make_cloud_event(text):
    """Creates a mock CloudEvent with base64-encoded message data."""
    event = MagicMock()
    encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
    event.data = {"message": {"data": encoded}}
    return event
