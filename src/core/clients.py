"""Lazy external-client factories.

Each getter is lru_cached and built on FIRST USE inside a function — never at
import — so the secret env vars (injected by Modal at container start) are read
at the right time. A missing key returns None, degrading just that integration.
"""

from functools import lru_cache

import google.genai as genai
import googlemaps
import spotipy
from googleapiclient.discovery import build
from notion_client import Client
from spotipy.oauth2 import SpotifyClientCredentials

from core.settings import get_settings


@lru_cache
def get_gemini_client():
    key = get_settings().gemini_api_key
    return genai.Client(api_key=key) if key else None


@lru_cache
def get_notion():
    token = get_settings().notion_integration_token
    return Client(auth=token, notion_version="2022-06-28") if token else None


@lru_cache
def get_spotify():
    s = get_settings()
    if not (s.spotify_client_id and s.spotify_client_secret):
        return None
    try:
        return spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=s.spotify_client_id, client_secret=s.spotify_client_secret
            )
        )
    except Exception:
        return None


@lru_cache
def get_gmaps():
    key = get_settings().google_places_api_key
    return googlemaps.Client(key=key) if key else None


@lru_cache
def get_youtube():
    key = get_settings().google_youtube_api_key
    if not key:
        return None
    try:
        return build("youtube", "v3", developerKey=key)
    except Exception as e:
        print(f"❌ Failed to init YouTube client: {e}")
        return None
