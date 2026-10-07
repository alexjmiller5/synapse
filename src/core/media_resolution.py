"""Resolve a capture within the credential's media allowlist, never general fan-out."""

import json

from google.genai import types

from core.ai_engine import (
    GEMINI_MODEL,
    generate_extraction_prompt,
    generate_with_retry,
    get_gemini_schema,
)
from core.external_data import enrich_context
from core.handlers import (
    Failed,
    _empty,
    handle_movies_tv_logic,
    handle_youtube_logic,
    handle_url_media,
)
from core.media_save import SaveReceipt
from core.pipeline import _classify_with_ai

KINDS = {
    "movies": "movie",
    "tv-shows": "tvShow",
    "youtube-videos": "youtubeVideo",
    "articles": "article",
    "podcasts": "podcastEpisode",
}
FIELDS = {"status": "Status", "tags": "Tags", "note": "Notes", "consumed_at": "Date Watched"}


def classify(text):
    return _classify_with_ai(text, "", [])[0]


def extract(category, text):
    context = enrich_context(category, text) if category == "youtube-videos" else None
    prompt = generate_extraction_prompt(category, text, context, [], "")
    response = generate_with_retry(
        model=GEMINI_MODEL,
        contents=[types.Content(parts=[types.Part(text=text)], role="user")],
        config=types.GenerateContentConfig(
            system_instruction=prompt,
            response_mime_type="application/json",
            response_json_schema=get_gemini_schema(category),
        ),
    )
    data = json.loads(response.text)
    if not isinstance(data, dict):
        raise ValueError("invalid extraction")
    return data


def write_resolved(category, data, *, checkpoint=None, explicit_properties=()):
    handler = (
        handle_url_media
        if category in ("podcasts", "articles")
        else handle_youtube_logic
        if category == "youtube-videos"
        else handle_movies_tv_logic
    )
    # The gateway must never create a cleanup task or enter another writer.
    return handler(
        category,
        data,
        receipt=True,
        review=lambda message: None,
        checkpoint=checkpoint,
        explicit_properties=explicit_properties,
        **({"strict_identity": True} if category in ("movies", "tv-shows") else {}),
    )


def resolve_capture(request, categories, fields, *, checkpoint=None):
    text = next(iter(request["input"].values()))
    if "url" in request["input"]:
        from urllib.parse import urlparse

        host = urlparse(text).hostname or ""
        category = (
            "youtube-videos"
            if host in ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
            else "podcasts"
            if host in ("open.spotify.com", "www.thisamericanlife.org", "thisamericanlife.org")
            else "articles"
        )
    else:
        category = classify(text)
    if category not in categories or category not in KINDS:
        return None, SaveReceipt("needs_review", "", reason="unsupported_category")
    data = extract(category, text)
    if "url" in request["input"]:
        data["Video URL" if category == "youtube-videos" else "URL"] = request["input"]["url"]
    field_map = {
        **FIELDS,
        "consumed_at": "Date Listened To"
        if category == "podcasts"
        else "Date Read"
        if category == "articles"
        else "Date Watched",
    }
    explicit = request.get("fields", {})
    for logical, value in explicit.items():
        if logical not in fields or logical not in field_map:
            return None, SaveReceipt("needs_review", "", reason="field_not_editable")
        data[field_map[logical]] = value
    for logical, prop in field_map.items():
        value = data.get(prop)
        if logical not in explicit and _empty(value):
            continue
        if logical not in fields:
            return None, SaveReceipt("needs_review", "", reason="field_not_editable")
        valid = value is None or (
            isinstance(value, list) and all(isinstance(tag, str) for tag in value)
            if logical == "tags"
            else isinstance(value, str)
        )
        if not valid:
            return None, SaveReceipt("needs_review", "", reason="invalid_field_type")
    if request["intent"] == "save" and "saved" not in fields:
        return None, SaveReceipt("needs_review", "", reason="field_not_editable")
    data["Capture Intent"] = request["intent"]
    save_checkpoint = (
        (lambda plan: checkpoint({**plan, "kind": KINDS[category]})) if checkpoint else None
    )
    result = write_resolved(
        category,
        data,
        checkpoint=save_checkpoint,
        explicit_properties=[field_map[field] for field in request.get("fields", {})],
    )
    if isinstance(result, Failed):
        result = SaveReceipt("needs_review", "", reason="resolution_failed")
    return KINDS[category], result
