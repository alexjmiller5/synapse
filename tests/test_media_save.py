"""Capture against a synthetic stateful hub; no personal catalog or network."""

from unittest.mock import patch

import pytest
from media_hub import SyntheticHub

from core import soma_hub
from core.pipeline import run_pipeline
from core.workflow import capture_scope
from helpers import make_gemini_response


def existing(status="Finished"):
    return {
        "id": "abc123",
        "status": status,
        "saved": 0,
        "note": "keep",
        "tags": '["Favorite"]',
        "updated_at": "2026-01-01T00:00:00.000Z",
        "hub_at": "2026-01-01T00:00:00.001Z",
        "deleted_at": None,
    }


CAPTURE = "5b0bf1f8-8d63-4c43-9a0a-4f7d0fb6a4f2"


def test_pipeline_resave_keeps_finished_and_unrequested_fields(mock_gemini, mock_youtube):
    hub = SyntheticHub(
        {
            "youtube_videos": {"abc123": existing()},
            "youtube_channels": {"channel-1": {"id": "channel-1"}},
        }
    )
    mock_gemini.models.generate_content.side_effect = [
        make_gemini_response({"category": "youtube-videos"}),
        make_gemini_response({"Video URL": "https://youtu.be/abc123", "Capture Intent": "save"}),
    ]
    mock_youtube.videos.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "snippet": {
                    "channelId": "channel-1",
                    "title": "Synthetic video",
                    "publishedAt": "2026-01-01T00:00:00Z",
                },
                "contentDetails": {"duration": "PT4M"},
            }
        ]
    }
    text = "save https://youtu.be/abc123 for later"
    capture = {"raw_text": text, "workspace": "default", "capture_id": CAPTURE}
    with (
        patch("core.soma_hub.requests.post", hub.post),
        patch("core.pipeline.enrich_context", return_value="Synthetic video"),
        capture_scope({}, capture),
    ):
        run_pipeline({"core_text": text}, [], {}, {}, [])
    row = hub.rows["youtube_videos"]["abc123"]
    assert row["status"] == "Finished"
    assert row["saved"] == 1
    assert row["note"] == "keep"
    assert row["tags"] == '["Favorite"]'


BINDING = {
    "table": "items",
    "editable_columns": ["saved", "status", "note", "tags", "date_watched"],
}


def save(hub, requested=None, **kwargs):
    from core.media_save import save_media

    with patch("core.soma_hub.requests.post", hub.post):
        return save_media(
            soma_hub,
            BINDING,
            "abc123",
            {"status": "Not Started"},
            requested or {"saved": 1},
            **kwargs,
        )


@pytest.mark.parametrize("status", ["Finished", "In Progress", "Priority"])
def test_save_only_changes_explicit_values(status):
    before = existing(status)
    hub = SyntheticHub({"items": {"abc123": before}})
    result = save(hub)
    assert result.state == "saved"
    assert result.identity == "abc123"
    row = hub.rows["items"]["abc123"]
    assert row["status"] == status and row["note"] == "keep" and row["tags"] == '["Favorite"]'
    assert row["saved"] == 1
    assert hub.writes[0][0] == "patch"
    assert hub.writes[0][1]["values"] == {"saved": 1}


def test_racing_poller_insert_keeps_its_values():
    hub = SyntheticHub()
    hub.before_insert = lambda rows: rows.update({"abc123": existing("In Progress")})
    result = save(hub)
    assert result.state == "saved"
    assert hub.rows["items"]["abc123"]["status"] == "In Progress"
    assert [w[0] for w in hub.writes] == ["insert", "patch"]


def test_tombstone_requires_review_and_never_restores():
    row = existing()
    row["deleted_at"] = "2026-01-01T00:00:00.000Z"
    hub = SyntheticHub({"items": {"abc123": row}})
    assert save(hub).state == "needs_review"
    assert hub.writes == []
    assert hub.rows["items"]["abc123"] == row


def test_concurrent_user_edit_returns_conflict_without_overwrite():
    hub = SyntheticHub({"items": {"abc123": existing()}})
    hub.before_patch = lambda row: row.update(
        status="Gave Up", updated_at="2026-01-03T00:00:00.000Z"
    )
    assert save(hub).state == "conflict"
    assert hub.rows["items"]["abc123"]["status"] == "Gave Up"
    assert hub.rows["items"]["abc123"]["saved"] == 0


@pytest.mark.parametrize("route", ["insert", "patch"])
def test_lost_reply_is_uncertain_and_retry_does_not_reset_status(route):
    hub = SyntheticHub({"items": {"abc123": existing()}} if route == "patch" else {})
    hub.lose_reply = route
    assert save(hub).state == "uncertain"
    hub.rows["items"]["abc123"]["status"] = "Finished"
    assert save(hub).state == "saved"
    assert hub.rows["items"]["abc123"]["status"] == "Finished"
    assert len(hub.writes) == 1


def test_consumption_does_not_implicitly_save_and_status_date_are_one_patch():
    hub = SyntheticHub({"items": {"abc123": existing("Not Started")}})
    requested = {"status": "Finished", "date_watched": "2026-01-02"}
    assert save(hub, requested).state == "saved"
    assert hub.rows["items"]["abc123"]["saved"] == 0
    assert hub.writes[0][1]["values"] == requested


def test_explicit_old_revision_cannot_be_rebased_onto_current_row():
    hub = SyntheticHub({"items": {"abc123": existing()}})
    assert save(hub, expected_revision={"updated_at": "old", "hub_at": "old"}).state == "conflict"
    assert hub.writes == []


def test_podcast_url_reuses_legacy_id_and_preserves_consumption(monkeypatch):
    from core.handlers import handle_url_media

    row = {
        **existing(),
        "id": "legacy-episode",
        "url": "https://open.spotify.com/episode/example?si=tracking",
    }
    hub = SyntheticHub({"podcast_episodes": {row["id"]: row}})
    monkeypatch.setattr(soma_hub.requests, "post", hub.post)
    monkeypatch.setattr("core.handlers.create_cleanup_task", lambda *a, **k: None)
    result = handle_url_media(
        "podcasts",
        {
            "URL": "https://open.spotify.com/episode/example",
            "Episode Title": "Example",
            "Capture Intent": "save",
        },
    )
    assert result == "podcast_episodes/legacy-episode"
    assert len(hub.rows["podcast_episodes"]) == 1
    assert hub.rows["podcast_episodes"]["legacy-episode"]["status"] == "Finished"
    assert hub.rows["podcast_episodes"]["legacy-episode"]["saved"] == 1


def test_article_capture_uses_the_pollers_canonical_url_identity(monkeypatch):
    from core.handlers import handle_url_media

    hub = SyntheticHub()
    monkeypatch.setattr(soma_hub.requests, "post", hub.post)
    data = {
        "URL": "https://example.test/story/?utm_source=test&b=2&a=1",
        "Title": "Example",
        "Capture Intent": "save",
    }
    assert handle_url_media("articles", data) == "articles/https://example.test/story?a=1&b=2"
    assert handle_url_media("articles", data) == "articles/https://example.test/story?a=1&b=2"
    assert len(hub.rows["articles"]) == 1


def test_tombstoned_legacy_podcast_never_gets_a_new_url_identity(monkeypatch):
    from core.handlers import handle_url_media, Failed

    row = {
        **existing(),
        "id": "legacy-episode",
        "url": "https://open.spotify.com/episode/example",
        "deleted_at": "gone",
    }
    hub = SyntheticHub({"podcast_episodes": {row["id"]: row}})
    monkeypatch.setattr(soma_hub.requests, "post", hub.post)
    monkeypatch.setattr("core.handlers.create_cleanup_task", lambda *a, **k: None)
    result = handle_url_media(
        "podcasts", {"URL": row["url"], "Episode Title": "Example", "Capture Intent": "save"}
    )
    assert isinstance(result, Failed)
    assert len(hub.rows["podcast_episodes"]) == 1 and hub.writes == []


def test_json_encoded_tags_are_not_rewritten_when_the_saved_intent_already_matches():
    hub = SyntheticHub({"items": {"abc123": {**existing(), "saved": 1}}})
    result = save(hub, {"saved": 1, "tags": ["Favorite"]})
    assert result.state == "saved"
    assert hub.writes == []


def test_current_hub_rejection_and_invalid_receipts_remain_typed_media_outcomes():
    import json
    import requests

    rejected = SyntheticHub()
    original = rejected.post

    def reject_insert(url, **kwargs):
        if not url.endswith("/insert"):
            return original(url, **kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {"inserted": [], "existing": [], "rejected": [{"id": "abc123"}]}
        ).encode()
        return response

    rejected.post = reject_insert
    assert save(rejected).state == "needs_review"
    malformed = SyntheticHub({"items": {"abc123": existing()}})
    transport = malformed.post

    def invalid_reply(url, **kwargs):
        response = transport(url, **kwargs)
        if url.endswith("/patch"):
            response._content = b'{"id":"abc123","revision":{"updated_at":"invalid","hub_at":null}}'
        return response

    malformed.post = invalid_reply
    assert save(malformed).state == "uncertain"
    assert len(malformed.writes) == 1
