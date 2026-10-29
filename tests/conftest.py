"""
Shared test fixtures for the Synapse test suite.

Mocking strategy:
- Fake secrets are seeded as env vars BEFORE core modules import, and the
  local workspace (core.workspace.local) is tests/fixtures/workspace: the
  product template plus the workflow bindings a real workspace carries
- The Soma catalog is tests/fixtures/catalog.json (generic values only),
  served by a fake `POST /v1/catalog/projection` (helpers.CatalogHub)
- The shared AI generation boundary and external clients are patched
  at the module level
- All external API calls are intercepted before any real network I/O
"""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

CATALOG = Path(__file__).parent / "fixtures" / "catalog.json"

# ---------------------------------------------------------------------------
# Fake secrets — seeded into the environment before core modules import
# ---------------------------------------------------------------------------
FAKE_SECRETS = {
    "ai-api-key": "fake-ai-key",
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
patch("spotipy.Spotify", return_value=MagicMock()).start()
patch("googleapiclient.discovery.build", return_value=MagicMock()).start()

# Import the clients module — the external constructors are patched above, so the
# lazy getters build MOCK clients. Each getter is lru_cached, so calling it here
# returns the same instance the code-under-test will get.
import core.clients as _clients_mod  # noqa: E402

_mock_ai = MagicMock()
_mock_spotify = _clients_mod.get_spotify()
_mock_youtube = _clients_mod.get_youtube()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_all_mocks(monkeypatch):
    from core import ai_engine

    monkeypatch.setattr(ai_engine, "_generate", _mock_ai)
    _mock_ai.reset_mock()
    _mock_ai.side_effect = None
    _mock_spotify.reset_mock()
    _mock_youtube.reset_mock()
    yield


@pytest.fixture(autouse=True)
def catalog_hub(monkeypatch):
    from core import catalog, workspace
    from helpers import CatalogHub

    hub = CatalogHub(json.loads(CATALOG.read_text()))
    monkeypatch.setattr(catalog, "requests", hub)
    workspace.local().catalog_memo = None
    yield hub
    workspace.local().catalog_memo = None


@pytest.fixture
def mock_ai():
    """Mock the shared generation boundary with plain JSON values."""
    return _mock_ai


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
