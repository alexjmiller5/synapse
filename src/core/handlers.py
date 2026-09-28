import re
import secrets
from datetime import datetime, timezone
from typing import NamedTuple

from core.config import DATABASES
from core.clients import get_youtube
from core.notion_utils import (
    create_page,
    create_cleanup_task,
    build_notion_properties,
)
from core.external_data import (
    get_youtube_video_id,
    resolve_tmdb_id,
    sanitize_youtube_url,
)
from core.life_hub import pull_ids, pull_rows, push_rows
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


def handle_hub_logic(category, data):
    """A capture for any category whose stanza names a life-data `hub_table`.

    The stanza's `columns` map extracted Notion-style property names to catalog
    columns; `constants` are fixed columns (things_to_do.kind); `match_on` names
    the natural key - a row already holding that value is UPDATED (only the
    columns we know are sent, so a status capture never clobbers the rest)
    instead of duplicated; `review_if_missing` turns an unfillable property into
    a `needs_review` reason rather than a Notion cleanup task. Everything the
    yaml used to enforce (required, allowlists, defaults) is the catalog's job
    now: a rejected row files a cleanup task and writes nothing.
    """
    stanza = DATABASES["databases"][category]
    table = stanza["hub_table"]
    row = {
        col: data[prop]
        for prop, col in stanza.get("columns", {}).items()
        if data.get(prop) not in (None, "", [])
    }
    row.update(stanza.get("constants") or {})
    for prop, reason in (stanza.get("review_if_missing") or {}).items():
        if data.get(prop) in (None, "", []):
            row["needs_review"] = reason

    match = stanza.get("match_on")
    row_id = None
    if match and row.get(match):
        key = str(row[match]).strip().lower()
        row_id = next(
            (
                r["id"]
                for r in pull_rows(table, [match])
                if str(r.get(match) or "").strip().lower() == key
            ),
            None,
        )
    row["id"] = row_id or secrets.token_hex(16)
    row["updated_at"] = now_utc_iso_ms()

    rejected = push_rows(table, [row]).get("rejected") or []
    if rejected:
        message = rejected[0].get("message")
        label = row.get(match) if match else next(iter(row.values()))
        create_cleanup_task(f"life-data rejected {label!r}: {message}")
        return Failed(f"life-data rejected {table}/{row['id']}: {message}")

    print(f"   ✅ Pushed {table}/{row['id']}")
    return f"{table}/{row['id']}"


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


def handle_people_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_default_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")
