import re
import json
import requests
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse
from inscriptis import get_text
from core.clients import spotify, youtube, gmaps
from core.notion_utils import create_cleanup_task

# Timestamp / tracking params to strip from YouTube URLs before storage
YOUTUBE_JUNK_PARAMS = ("t", "si", "feature")


def sanitize_youtube_url(url):
    """Strips timestamp/tracking params (t, si, feature) from a YouTube URL."""
    parsed = urlparse(url)
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k not in YOUTUBE_JUNK_PARAMS
        ]
    )
    return urlunparse(parsed._replace(query=query))


def extract_url(text):
    # Regex Explanation:
    # 1. (https?://)?  -> Optional Protocol
    # 2. (www\.)?      -> Optional www
    # 3. [\w-]+\.      -> Domain name (e.g. 'google.')
    # 4. [\w.]{2,}     -> TLD (e.g. 'com', 'co.uk')
    # 5. \S* -> Any trailing path/query
    match = re.search(
        r"\b((?:https?://)?(?:www\.)?[\w-]+\.[\w.]{2,}\S*)", text, re.IGNORECASE
    )

    if match:
        url = match.group(1)
        # Prepend https:// if missing so requests library doesn't fail
        if not url.startswith(("http://", "https://")):
            return f"https://{url}"
        return url
    return None


def fetch_web_metadata(url):
    """Fetches Page Title (Preferring Open Graph) AND Body Text."""
    print(f"   ⏳ Fetching Web Metadata for: {url}...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=5)
        res.raise_for_status()

        # 1. Scrape Body Text
        full_text = get_text(res.text)
        cleaned_body = re.sub(r"\s+", " ", full_text).strip()[:1500]

        # 2. Get Title (Strategy: Open Graph -> Standard Title)

        # A. Try Open Graph Title first (Usually cleaner/better)
        og_match = re.search(
            r'<meta property="og:title" content="(.*?)"', res.text, re.IGNORECASE
        )

        # B. Fallback to standard <title> tag
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>", res.text, re.IGNORECASE | re.DOTALL
        )

        title = "No Title Found"

        if og_match:
            title = og_match.group(1).strip()
            print(f"   ✅ Scraped (OG): {title}")
        elif title_match:
            title = title_match.group(1).strip()
            print(f"   ✅ Scraped (HTML): {title}")

        # --- GLOBAL CLEANUP ---
        # 1. Fix HTML entities
        title = (
            title.replace("–", "-")
            .replace(" ", " ")
            .replace("&", "&")
            .replace("'", "'")
        )

        # 2. Remove "GitHub - " prefix (Because GitHub forces this in the title tag)
        if title.startswith("GitHub - "):
            title = title.replace("GitHub - ", "", 1)

        return f"HTML Title: {title}\nPage Content Preview:\n{cleaned_body}..."

    except Exception as e:
        print(f"   ⚠️ Web fetch failed: {e}")
        # FALLBACK: Create Task
        print("   🧹 Triggering cleanup task for failed web scrape...")
        # create_cleanup_task(f"Manual Bookmark Entry (Scrape Failed): {url}", link_url=url)
        return "Error fetching metadata"


def get_tal_metadata(url):
    print(f"   ⏳ Fetching URL metadata from: {url}...")
    try:
        # 1. Fake User-Agent to avoid blocking
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }

        # 2. Timeout is KEY here. If it takes >5s, we abort and trigger the cleanup task.
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        full_text = get_text(response.text)
        cleaned_text = re.sub(r"\s+", " ", full_text).strip()[:2000]

        print("   ✅ Metadata fetched successfully.")
        return f"Content:\n{cleaned_text}..."

    except Exception as e:
        print(f"   ⚠️ Metadata fetch failed: {e}")

        # 3. VERBOSE FALLBACK: Create the Task immediately
        print("   🧹 Triggering cleanup task for failed scrape...")
        create_cleanup_task(
            f"Manual Podcast Entry (Scraping Failed): {url}", link_url=url
        )

        return f"Error fetching URL: {e} (User has been notified via a Cleanup Task)"


def get_spotify_metadata(url):
    if not spotify:
        return "No Spotify Client"
    try:
        r = spotify.episode(url)
        return f"Show: {r['show']['name']}\nEp: {r['name']}\nDesc: {r['description']}"
    except Exception as e:
        return f"Spotify Error: {e}"


def get_video_channel_details(url):
    """
    Fetches official Channel Title and Channel ID from a Video URL.
    """
    if not youtube:
        return None

    video_id = get_youtube_video_id(url)
    if not video_id:
        return None

    try:
        # Get Video Details (which includes Channel ID)
        request = youtube.videos().list(part="snippet", id=video_id)
        response = request.execute()

        if not response.get("items"):
            return None

        snippet = response["items"][0]["snippet"]
        channel_title = snippet.get("channelTitle")
        channel_id = snippet.get("channelId")

        return {
            "title": channel_title,
            "id": channel_id,
            "url": f"https://www.youtube.com/channel/{channel_id}",
        }

    except Exception as e:
        print(f"   ⚠️ Failed to fetch channel details: {e}")
        return None


def get_youtube_video_id(url):
    """Parses Video ID from various YouTube URL formats."""
    parsed = urlparse(url)
    if parsed.hostname in ["youtu.be"]:
        return parsed.path[1:]
    if parsed.hostname in ["www.youtube.com", "youtube.com", "m.youtube.com"]:
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            return qs.get("v", [None])[0]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]
    return None


def get_youtube_metadata(url):
    if not youtube:
        return "No YouTube Client Configured"

    video_id = get_youtube_video_id(url)
    if not video_id:
        return "Could not extract Video ID"

    try:
        request = youtube.videos().list(part="snippet", id=video_id)
        response = request.execute()

        if not response.get("items"):
            return "Video not found or private."

        item = response["items"][0]
        snippet = item["snippet"]

        title = snippet.get("title")
        channel_title = snippet.get("channelTitle")
        # Notion logic expects a handle if possible, but channel title works for search too
        # API doesn't return @handle in snippet, so we use Channel Title.

        return f"Title: {title}\nHandle: {channel_title}"

    except Exception as e:
        print(f"   ⚠️ YT Metadata fetch failed: {e}")
        print("   🧹 Triggering cleanup task for failed YT extraction...")
        # create_cleanup_task(f"Manual YouTube Entry (Extraction Failed): {url}", link_url=url)
        return f"YT Error: {e}"


def resolve_final_url(url):
    """
    Follows redirects to get the real Google Maps URL.
    """
    print(f"   🔍 Resolving URL: {url}")
    try:
        # We use a HEAD request to follow redirects without downloading body
        response = requests.get(url, allow_redirects=True, timeout=5)
        print(f"   ✅ Resolved URL to: {response.url}")
        return response.url
    except Exception as e:
        print(f"   ⚠️ URL Resolution failed: {e}")
        return url


def get_place_details(query):
    """
    Fetches details from Google Places API.
    Returns RAW types for the AI to map.
    """
    if not gmaps:
        print("   ❌ Google Maps Client is NOT initialized. Skipping.")
        return None

    # NEW: Resolve URL if it looks like a link
    if query.startswith("http"):
        query = resolve_final_url(query)

    print(f"🗺️ Fetching Google Place Details for Query: '{query}'")
    try:
        # 1. Text Search to get Place ID
        print("   -> Calling gmaps.find_place...")
        resp = gmaps.find_place(
            input=query, input_type="textquery", fields=["place_id"]
        )

        if resp["status"] != "OK" or not resp["candidates"]:
            print(f"   ⚠️ No place found. Response Status: {resp.get('status')}")
            return None

        place_id = resp["candidates"][0]["place_id"]
        print(f"   -> Found Place ID: {place_id}")

        # 2. Get Full Details
        print(f"   -> Fetching full details for {place_id}...")
        details = gmaps.place(
            place_id=place_id,
            fields=[
                "name",
                "formatted_address",
                "address_component",
                "type",
                "url",
                "website",
            ],
        )
        result = details.get("result", {})

        # 3. Extract City/Country
        city = None
        country = None
        for comp in result.get("address_components", []):
            types = comp.get("types", [])
            if "locality" in types:
                city = comp["long_name"]
            elif "country" in types:
                country = comp["long_name"]

        print("   ✅ Google Maps Data Retrieved:")
        print(f"      - Name: {result.get('name')}")
        print(f"      - Address: {result.get('formatted_address')}")
        print(f"      - City/Country: {city}, {country}")
        print(f"      - Types: {result.get('types', [])}")

        # 4. Return Raw Data for AI
        return {
            "Name": result.get("name"),
            "Address": result.get("formatted_address"),
            "City": city,
            "Country": country,
            "Google Maps URL": result.get("url"),
            "Raw Types": result.get("types", []),  # AI will map these to Notion Tags
        }
    except Exception as e:
        print(f"❌ Google Maps Error: {e}")
        return None


def enrich_context(category, raw_text):
    url = extract_url(raw_text)

    # 1. Google Places (Prioritize URL, fallback to raw text if needed)
    if category == "places":
        print("   🔗 Enriched Context Triggered for Places")
        query = url if url else raw_text
        print(f"      - Using Query: {query}")

        details = get_place_details(query)
        if details:
            print("      ✅ Context successfully retrieved from Google Maps.")
            return f"--- GOOGLE MAPS DATA ---\n{json.dumps(details)}"

        print("      ⚠️ No context returned from Google Maps.")
        return None

    # 2. Existing Logic
    if not url:
        return None

    print(f"   🔗 Enriched Context Triggered for: {url}")

    if category == "podcasts":
        if "spotify.com" in url:
            return get_spotify_metadata(url)
        if "thisamericanlife" in url:
            return get_tal_metadata(url)
    elif category == "youtube-videos":
        return get_youtube_metadata(url)
    elif category == "bookmarks":
        return fetch_web_metadata(url)

    return None
