"""Capture against a synthetic stateful hub; no personal catalog or network."""

from unittest.mock import patch

import pytest
from media_hub import SyntheticHub

from core import life_hub
from core.pipeline import run_pipeline
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
    with (
        patch("core.life_hub.requests.post", hub.post),
        patch("core.pipeline.enrich_context", return_value="Synthetic video"),
    ):
        run_pipeline({"core_text": "save https://youtu.be/abc123 for later"}, [], {}, {}, [])
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

    with patch("core.life_hub.requests.post", hub.post):
        return save_media(
            life_hub,
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
