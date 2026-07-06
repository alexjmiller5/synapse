import google.genai as genai
from notion_client import Client
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from googleapiclient.discovery import build
import googlemaps
from core.secrets import get_secret

gemini_client = None
notion = None
spotify = None
gmaps = None

GEMINI_API_KEY = get_secret("gemini-api-key")
NOTION_API_KEY = get_secret("notion-integration-token")
SPOTIFY_CLIENT_ID = get_secret("spotify-client-id")
SPOTIFY_CLIENT_SECRET = get_secret("spotify-client-secret")
GOOGLE_PLACES_KEY = get_secret("google-places-api-key")
YOUTUBE_API_KEY = get_secret("google-youtube-api-key")

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
if NOTION_API_KEY:
    notion = Client(auth=NOTION_API_KEY, notion_version="2022-06-28")
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
            )
        )
    except Exception:
        pass

if GOOGLE_PLACES_KEY:
    gmaps = googlemaps.Client(key=GOOGLE_PLACES_KEY)

if YOUTUBE_API_KEY:
    try:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    except Exception as e:
        print(f"❌ Failed to init YouTube client: {e}")
