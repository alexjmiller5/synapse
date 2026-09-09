import re
from datetime import datetime, timezone
from typing import NamedTuple

from core.config import DATABASES
from core.secrets import get_db_id
from core.clients import get_notion, get_youtube
from core.notion_utils import (
    create_page,
    update_status,
    create_cleanup_task,
    fetch_existing_page,
    build_notion_properties,
)
from core.external_data import (
    get_youtube_video_id,
    resolve_tmdb_id,
    sanitize_youtube_url,
)
from core.life_hub import pull_ids, push_rows
from core.timeutils import now_utc_iso_ms

# Same regex as media-center's core/youtube.py - kept in sync by hand, not shared,
# because the two services don't share a dependency.
_ISO8601_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _parse_duration_s(iso):
    d, h, m, s = (int(x or 0) for x in _ISO8601_DURATION.fullmatch(iso).groups())
    return d * 86400 + h * 3600 + m * 60 + s


def _to_hub_datetime(value):
    """Normalize a YouTube ISO-8601 UTC timestamp to the hub's required shape.

    The hub validates `datetime` columns as ISO-8601 UTC WITH milliseconds;
    YouTube's `snippet.publishedAt` comes back as e.g. `...Z` with no
    fractional seconds, which the hub rejects outright.
    """
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def known_channel_ids():
    return pull_ids("youtube_channels")


class Failed(NamedTuple):
    """A handler outcome that created nothing (a cleanup task was filed instead).

    The pipeline logs it as Error(s) with no Created Item - returning None here
    would be indistinguishable from a successful write with no URL, and the
    Executions log would claim Success over an empty result.
    """

    detail: str


def handle_groceries_fun_logic(category, data, inventory_map):
    # Map 'Name' vs 'Title' depending on DB
    search_key = "Name" if category == "groceries" else "Title"
    search_val = data.get(search_key)

    if category == "groceries" and inventory_map and search_val in inventory_map:
        page_id = inventory_map[search_val]
        print(f"   ✅ Groceries: Matched '{search_val}' (ID: {page_id}). Updating...")
        return update_status(page_id, data.get("Status"), category).get("url")

    # For Fun Activities, perform a smart search
    if category == "fun-activities":
        # Check for duplicates first
        existing_id = fetch_existing_page(category, search_val, key="Title")
        if existing_id:
            print(f"   ✅ Fun Activities: Matched '{search_val}'. Updating Status...")
            return update_status(existing_id, data.get("Status"), category).get("url")

        # Create new
        print(f"   ✨ Creating new {category} page.")
        resp = create_page(category, build_notion_properties(category, data))
        created_url = resp.get("url")

        # Check for Location Ambiguity (After creation, so we have a link)
        if not data.get("Location"):
            print("   ⚠️ Fun Activity Location Unknown. Creating cleanup task.")
            create_cleanup_task(f"Classify Location for: {search_val}", link_url=created_url)

        return created_url

    print(f"   ✨ Creating new {category} page.")
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_youtube_logic(category, data):
    """YouTube captures are a life-data table, not a Notion DB.

    A channel is pushed once (on first sight of a video from it), with a
    "Classify new Channel" cleanup task so Alex sets follow/subscription by
    hand; every later video from that channel just links channel_id. Channel
    membership is checked against the hub's actual state (known_channel_ids),
    never an in-run cache.
    """
    url = sanitize_youtube_url(data["Video URL"]) if data.get("Video URL") else None
    vid = get_youtube_video_id(url) if url else None
    if not vid:
        raise ValueError(f"No YouTube video ID in URL: {data.get('Video URL')!r}")

    yt = get_youtube()
    if not yt:
        create_cleanup_task(f"No YouTube client configured for video {vid}")
        return Failed(f"No YouTube client configured for video {vid}")

    video_items = (
        yt.videos().list(part="snippet,contentDetails", id=vid).execute().get("items") or []
    )
    if not video_items:
        create_cleanup_task(f"YouTube video not found: {vid}")
        return Failed(f"YouTube video not found: {vid}")
    item = video_items[0]
    snippet = item["snippet"]
    channel_id = snippet["channelId"]

    if channel_id not in known_channel_ids():
        channel_items = (
            yt.channels().list(part="snippet,contentDetails", id=channel_id).execute().get("items")
            or []
        )
        if not channel_items:
            create_cleanup_task(f"YouTube channel not found: {channel_id}")
            return Failed(f"YouTube channel not found: {channel_id}")
        ch = channel_items[0]
        title = ch["snippet"]["title"]
        channel_row = {
            "id": channel_id,
            "title": title,
            "handle": ch["snippet"].get("customUrl"),
            "channel_url": f"https://www.youtube.com/channel/{channel_id}",
            "uploads_playlist_id": ch["contentDetails"]["relatedPlaylists"]["uploads"],
            "follow": 0,
            "backfilled": 0,
            "subscription": "Never Subscribed",
            "updated_at": now_utc_iso_ms(),
        }
        push_rows("youtube_channels", [channel_row])
        create_cleanup_task(f"Classify new Channel: {title}")

    # A live/premiere video has no fixed duration yet - the row is still valid
    # without it, just not resolvable as a short.
    duration = item["contentDetails"].get("duration")
    duration_s = _parse_duration_s(duration) if duration else None
    status = data.get("Status") or "Not Started"
    video_row = {
        "id": vid,
        "channel_id": channel_id,
        "title": snippet["title"],
        "published_at": _to_hub_datetime(snippet.get("publishedAt")),
        "duration_s": duration_s,
        "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "is_short": 1 if duration_s is not None and duration_s <= 180 else 0,
        "status": status,
        "updated_at": now_utc_iso_ms(),
    }
    if data.get("Tags"):
        video_row["tags"] = data["Tags"]

    rejected = push_rows("youtube_videos", [video_row]).get("rejected") or []
    if rejected:
        message = rejected[0].get("message")
        create_cleanup_task(f"life-data rejected {snippet['title']!r}: {message}")
        return Failed(f"life-data rejected youtube_videos/{vid}: {message}")

    print(f"   ✅ Pushed youtube_videos/{vid}")
    return f"youtube_videos/{vid}"


def handle_movies_tv_logic(category, data):
    """Movies and TV shows are life-data rows, not Notion pages.

    The TMDB id IS the row id, so an unconfident match is worse than none: we
    file a cleanup task and write nothing rather than pin a row to the wrong
    film. Everything else about the title (genres, cast, poster) is derived on
    the hub - we push only the columns we actually know, and the hub's upsert
    touches only those, so a status capture never clobbers tags or date_watched.
    """
    table = DATABASES["databases"][category]["hub_table"]
    kind = "movie" if category == "movies" else "tv"
    title = data.get("Title")

    tmdb_id = resolve_tmdb_id(kind, title)
    if not tmdb_id:
        create_cleanup_task(f"Could not resolve {title!r} on TMDB ({category})")
        return Failed(f"No confident TMDB match for {title!r} - nothing written")

    # The extractor emits "" for a field it could not fill; status is required.
    status = data.get("Status") or "Not Started"
    row = {"id": tmdb_id, "status": status, "updated_at": now_utc_iso_ms()}
    if data.get("Tags"):
        row["tags"] = data["Tags"]

    rejected = push_rows(table, [row]).get("rejected") or []
    if rejected:
        message = rejected[0].get("message")
        create_cleanup_task(f"life-data rejected {title!r}: {message}")
        return Failed(f"life-data rejected {table}/{tmdb_id}: {message}")

    print(f"   ✅ Pushed {table}/{tmdb_id}")
    return f"{table}/{tmdb_id}"


def handle_bookmarks_logic(category, data):
    target_url = data.get("URL")

    # House style: bookmark descriptions never end with a period (the prompt
    # says so too, but never trust the AI to comply).
    if isinstance(data.get("Description"), str):
        data["Description"] = data["Description"].rstrip(".")

    # A. Check for Duplicates (Exact URL Match)
    if target_url:
        db_id = get_db_id("bookmarks")
        try:
            # Specific query for URL property type
            resp = get_notion().request(
                path=f"databases/{db_id}/query",
                method="POST",
                body={"filter": {"property": "URL", "url": {"equals": target_url}}},
            )
            if resp.get("results"):
                print(f"   ✅ Bookmark already exists: {target_url}")
                return f"https://www.notion.so/{resp['results'][0]['id'].replace('-', '')}"
        except Exception as e:
            print(f"   ⚠️ Bookmark duplicate check failed: {e}")

    # B. Apply Logic (GitHub Tags)
    if "github.com" in target_url:
        tags = data.get("Tags", [])
        if isinstance(tags, list) and "Github" not in tags:
            tags.append("Github")
            data["Tags"] = tags

    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_people_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_bucket_list_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_default_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")
