"""Pytest conftest — mock GCP and Notion dependencies before any processor imports."""

import sys
from unittest.mock import MagicMock

# Mock GCP Secret Manager before any processor module is imported
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.secretmanager"] = MagicMock()
sys.modules["google.genai"] = MagicMock()
sys.modules["google.genai.types"] = MagicMock()
sys.modules["notion_client"] = MagicMock()
sys.modules["spotipy"] = MagicMock()
sys.modules["spotipy.oauth2"] = MagicMock()
sys.modules["googlemaps"] = MagicMock()
sys.modules["googleapiclient"] = MagicMock()
sys.modules["googleapiclient.discovery"] = MagicMock()
sys.modules["inscriptis"] = MagicMock()
