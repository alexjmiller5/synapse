"""
Shared test fixtures for the Synapse test suite.

Mocking strategy:
- Fake secrets are seeded as env vars BEFORE core modules import, and the
  local workspace (core.workspace.local) is tests/fixtures/workspace: the
  product template plus the workflow bindings a real workspace carries
- The Soma catalog is tests/fixtures/catalog.json (generic values only),
  served by a fake `GET /v1/catalog`
- core.clients module globals (gemini_client, spotify, youtube) are patched
  at the module level
- All external API calls are intercepted before any real network I/O
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

CATALOG = Path(__file__).parent / "fixtures" / "catalog.json"

# ---------------------------------------------------------------------------
# Fake secrets — seeded into the environment before core modules import
# ---------------------------------------------------------------------------
FAKE_SECRETS = {
    "gemini-api-key": "fake-gemini-key",
    "spotify-client-id": "fake-spotify-id",
    "spotify-client-secret": "fake-spotify-secret",
    "google-youtube-api-key": "fake-youtube-key",
    # the workspace's Soma hub
    "soma-hub-url": "https://hub.test.invalid",
    "soma-hub-token": "fake-hub-token",
}

for _sid, _val in FAKE_SECRETS.items():
    os.environ.setdefault(_sid.upper().replace("-", "_"), _val)
os.environ["SYNAPSE_WORKSPACE_DIR"] = os.path.join(
    os.path.dirname(__file__), "fixtures", "workspace"
)

# Patch external client constructors BEFORE core.clients is imported
patch("google.genai.Client", return_value=MagicMock()).start()
patch("spotipy.Spotify", return_value=MagicMock()).start()
patch("googleapiclient.discovery.build", return_value=MagicMock()).start()

# Import the clients module — the external constructors are patched above, so the
# lazy getters build MOCK clients. Each getter is lru_cached, so calling it here
# returns the same instance the code-under-test will get.
import core.clients as _clients_mod  # noqa: E402

_mock_gemini = _clients_mod.get_gemini_client()
_mock_spotify = _clients_mod.get_spotify()
_mock_youtube = _clients_mod.get_youtube()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_all_mocks():
    """Auto-reset all mocks before every test to prevent state bleed."""
    _mock_gemini.reset_mock()
    _mock_gemini.models.generate_content.side_effect = None
    _mock_spotify.reset_mock()
    _mock_youtube.reset_mock()
    yield


class _CatalogHub:
    """The hub's `GET /v1/catalog`, serving the fixture catalog."""

    def __init__(self):
        self.raw = json.loads(CATALOG.read_text())

    def get(self, url, *, headers, timeout):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(self.raw).encode()
        response.headers["ETag"] = '"fixture"'
        return response


@pytest.fixture(autouse=True)
def catalog_hub(monkeypatch):
    from core import catalog, workspace

    hub = _CatalogHub()
    monkeypatch.setattr(catalog, "requests", hub)
    workspace.local().catalog_memo = None
    yield hub
    workspace.local().catalog_memo = None


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
def media_hub(monkeypatch):
    from media_hub import SyntheticHub
    from core import soma_hub

    hub = SyntheticHub()
    monkeypatch.setattr(soma_hub.requests, "post", hub.post)
    return hub
