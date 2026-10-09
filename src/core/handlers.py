import re
import secrets
from datetime import datetime, timezone
from typing import NamedTuple

from core.config import CATEGORIES
from core.clients import get_youtube
from core.external_data import (
    get_youtube_video_id,
    resolve_tmdb_id,
    sanitize_youtube_url,
)
from core.soma_hub import pull_ids, pull_rows, push_rows
from core import soma_hub
from core.media_save import save_media
from core.timeutils import now_utc_iso_ms
from core.workflow import create_cleanup_task

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


def _empty(value):
    # A pulled multi_select comes back as JSON text, so "[]" is empty too.
    return value in (None, "", [], "[]")


def handle_hub_logic(category, data):
    """A capture for any category whose stanza maps fields to `columns`.

    The stanza's `columns` map extracted field names to catalog columns; `constants` are fixed columns (things_to_do.kind); `match_on` names
    the natural key - a row already holding that value is UPDATED (only the
    columns we know are sent, so a status capture never clobbers the rest)
    instead of duplicated; `review_if_missing` turns an unfillable property into
    a `needs_review` reason rather than a cleanup task (only when the matched
    row lacks it too; a capture that fills it clears that reason).
    `data["_fill_only"]` names properties the caller only guessed: they fill
    an empty column but never replace a value the matched row already holds.
    The catalog enforces the contract (required, options, defaults,
    invariants): a rejected row files a cleanup task and writes nothing.
    """
    stanza = CATEGORIES[category]
    table = stanza["table"]
    columns = stanza.get("columns", {})
    review = stanza.get("review_if_missing") or {}
    fill_only = data.get("_fill_only") or []
    row = {col: data[prop] for prop, col in columns.items() if not _empty(data.get(prop))}
    row.update(stanza.get("constants") or {})

    match = stanza.get("match_on")
    known = {}
    if match and row.get(match):
        key = str(row[match]).strip().lower()
        wanted = [match] + [columns[p] for p in [*review, *fill_only]]
        wanted += ["needs_review"] if review else []
        known = next(
            (r for r in pull_rows(table, wanted) if str(r.get(match) or "").strip().lower() == key),
            {},
        )

    for prop in fill_only:
        if not _empty(known.get(columns[prop])):
            row.pop(columns[prop], None)
    for prop, reason in review.items():
        if not _empty(data.get(prop)):
            if known.get("needs_review") == reason:
                row["needs_review"] = None
        elif _empty(known.get(columns[prop])):
            row["needs_review"] = reason

    row["id"] = known.get("id") or secrets.token_hex(16)
    row["updated_at"] = now_utc_iso_ms()

    rejected = push_rows(table, [row]).get("rejected") or []
    if rejected:
        message = rejected[0].get("message")
        label = row.get(match) if match else next(iter(row.values()))
        create_cleanup_task(f"soma rejected {label!r}: {message}")
        return Failed(f"soma rejected {table}/{row['id']}: {message}")

    print(f"   ✅ Pushed {table}/{row['id']}")
    return f"{table}/{row['id']}"


def _capture_media(
    category,
    identity,
    initializer,
    data,
    *,
    receipt=False,
    review=None,
    checkpoint=None,
    explicit_properties=(),
):
    review = review or create_cleanup_task
    stanza = CATEGORIES[category]
    mapping = stanza["capture_columns"]
    requested = {
        column: data[field]
        for field, column in mapping.items()
        if field in explicit_properties or not _empty(data.get(field))
    }
    saved_column = stanza.get("saved_column")
    if data.get("Capture Intent") == "save":
        if not saved_column:
            return Failed("Explicit saves require a configured saved field")
        requested[saved_column] = 1
    result = save_media(
        soma_hub,
        {
            "table": stanza["table"],
            "editable_columns": [*mapping.values(), *([saved_column] if saved_column else [])],
        },
        identity,
        initializer,
        requested,
        checkpoint=checkpoint,
    )
    if result.state != "saved":
        review(f"Media capture requires review: {result.reason}")
        return result if receipt else Failed(f"Media capture {result.state}: {result.reason}")
    return result if receipt else f"{stanza['table']}/{identity}"


def handle_youtube_logic(
    category, data, *, receipt=False, review=None, checkpoint=None, explicit_properties=()
):
    """A YouTube capture: the video id is the row id, its facts come from the API.

    A channel is pushed once (on first sight of a video from it), with a
    "Classify new Channel" cleanup task so the user chooses follow by
    hand; every later video from that channel just links channel_id. Channel
    membership is checked against the hub's actual state (known_channel_ids),
    never an in-run cache.
    """
    review = review or create_cleanup_task
    url = sanitize_youtube_url(data["Video URL"]) if data.get("Video URL") else None
    vid = get_youtube_video_id(url) if url else None
    if not vid:
        review(f"No YouTube video ID in URL: {data.get('Video URL')}")
        return Failed(f"No YouTube video ID in URL: {data.get('Video URL')!r}")

    yt = get_youtube()
    if not yt:
        review(f"No YouTube client configured for video {vid}")
        return Failed(f"No YouTube client configured for video {vid}")

    video_items = (
        yt.videos().list(part="snippet,contentDetails", id=vid).execute().get("items") or []
    )
    if not video_items:
        review(f"YouTube video not found: {vid}")
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
            review(f"YouTube channel not found: {channel_id}")
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
            "updated_at": now_utc_iso_ms(),
        }
        channel_receipt = save_media(
            soma_hub,
            {"table": "youtube_channels", "editable_columns": []},
            channel_id,
            channel_row,
            {},
        )
        if channel_receipt.state != "saved":
            return Failed(f"Channel capture {channel_receipt.state}: {channel_receipt.reason}")
        review(f"Classify new Channel: {title}")

    # A live/premiere video has no fixed duration yet - the row is still valid
    # without it, just not resolvable as a short.
    duration = item["contentDetails"].get("duration")
    duration_s = _parse_duration_s(duration) if duration else None
    video_row = {
        "id": vid,
        "channel_id": channel_id,
        "title": snippet["title"],
        "published_at": _to_hub_datetime(snippet.get("publishedAt")),
        "duration_s": duration_s,
        "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "is_short": 1 if duration_s is not None and duration_s <= 180 else 0,
        "status": "Not Started",
        "updated_at": now_utc_iso_ms(),
    }
    return _capture_media(
        category,
        vid,
        video_row,
        data,
        receipt=receipt,
        review=review,
        checkpoint=checkpoint,
        explicit_properties=explicit_properties,
    )


def handle_movies_tv_logic(
    category,
    data,
    *,
    receipt=False,
    review=None,
    checkpoint=None,
    explicit_properties=(),
    strict_identity=False,
):
    """Movies and TV shows: the TMDB id IS the row id, so an unconfident match is worse than none: we
    file a cleanup task and write nothing rather than pin a row to the wrong
    film. Everything else about the title (genres, cast, poster) is derived on
    the hub - we push only the columns we actually know, and the hub's upsert
    touches only those, so a status capture never clobbers tags or date_watched.
    """
    review = review or create_cleanup_task
    kind = "movie" if category == "movies" else "tv"
    title = data.get("Title")

    tmdb_id = (
        resolve_tmdb_id(kind, title, strict=True)
        if strict_identity
        else resolve_tmdb_id(kind, title)
    )
    if not tmdb_id:
        review(f"Could not resolve {title!r} on TMDB ({category})")
        return Failed(f"No confident TMDB match for {title!r} - nothing written")

    return _capture_media(
        category,
        tmdb_id,
        {"status": "Not Started"},
        data,
        receipt=receipt,
        review=review,
        checkpoint=checkpoint,
        explicit_properties=explicit_properties,
    )


def canonical_media_url(url):
    """The media poller's public URL identity rules; no personal source list."""
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(url.strip())
    if (
        parsed.scheme.lower() not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("invalid media URL")
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.startswith("utm_") and key not in {"si", "ref", "fbclid", "gclid"}
    )
    path = parsed.path.rstrip("/") if len(parsed.path) > 1 else parsed.path
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), "")
    )


def handle_url_media(
    category, data, *, receipt=False, review=None, checkpoint=None, explicit_properties=()
):
    stanza = CATEGORIES[category]
    try:
        raw_url = (data.get("URL") or "").strip()
        url = canonical_media_url(raw_url)
        if category == "podcasts":
            identity = soma_hub.media_url_identity(stanza["table"], url, canonical_media_url)
        else:
            # Some producer IDs deliberately retain fragments and trailing slashes.
            # Check exact identity before applying ordinary URL normalization.
            exact = soma_hub.read_row(stanza["table"], raw_url, ["id"])
            if exact:
                identity = raw_url
            elif "#" in raw_url:
                return Failed("Unknown fragment identity requires review")
            else:
                identity = url
    except (ValueError, TypeError):
        return Failed("Media URL requires identity review")
    user_columns = set(stanza["capture_columns"].values())
    initializer = {
        column: data[prop]
        for prop, column in stanza.get("columns", {}).items()
        if column not in user_columns and not _empty(data.get(prop))
    }
    if category == "podcasts":
        initializer["url"] = url
    initializer["status"] = "Not Started"
    return _capture_media(
        category,
        identity,
        initializer,
        data,
        receipt=receipt,
        review=review,
        checkpoint=checkpoint,
        explicit_properties=explicit_properties,
    )
