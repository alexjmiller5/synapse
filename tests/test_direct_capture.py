"""General captures land verbatim before any recognition or agent work."""

import copy

import pytest
import requests

from core import pipeline, soma_hub, workspace
from core.workflow import capture_scope
from helpers import make_gemini_response


PAYLOAD = {
    "raw_text": "Save this URL https://example.invalid/a@b $ Project tomorrow\nSecond action",
    "source": "test-client",
    "workspace": "local",
    "capture_id": "45601840-1df2-47e5-9eb3-9175ed2932e3",
}


@pytest.fixture
def direct(monkeypatch):
    selected = copy.deepcopy(workspace.current())
    selected.config["workflow"].pop("capture_mode", None)
    payload = {**PAYLOAD, "workspace": selected.id}

    def forbidden(*args, **kwargs):
        raise AssertionError("Direct capture must not invoke recognition or context reads")

    for name in (
        "parse_raw_input",
        "generate_with_retry",
        "fetch_active_projects",
        "fetch_inventory_map",
        "get_settings",
    ):
        monkeypatch.setattr(pipeline, name, forbidden)
    with workspace.use(selected):
        yield selected, payload


def test_default_capture_preserves_all_text_and_requests_dispatch(direct, media_hub):
    _, payload = direct
    store = {}
    pipeline.run(payload, store)
    pipeline.run(payload, store)
    (task,) = media_hub.rows["tasks"].values()
    (execution,) = media_hub.rows["synapse_executions"].values()
    assert task["notes"] == payload["raw_text"]
    assert task["dispatch"] == "Requested"
    assert task["lane"] == "AI"
    assert task["priority"] == "High"
    assert task["status"] == "To Do"
    assert task["tags"] == ["Chore"]
    assert len(task["title"]) <= 120
    assert execution["raw_input"] == payload["raw_text"]
    assert execution["source"] == "test-client"
    assert execution["created_item"] == f"tasks/{task['id']}"
    assert execution["code_execution"] == "Success"


@pytest.mark.parametrize("column", ["Name", "Notes", "Dispatch", "Lane"])
def test_missing_capture_mapping_fails_before_writes(direct, media_hub, column):
    selected, payload = direct
    del selected.config["workflow"]["tasks"]["columns"][column]
    with pytest.raises(ValueError, match=column):
        pipeline.run(payload, {})
    assert not any(media_hub.rows.values())


def test_lost_task_receipt_reuses_task_and_frozen_route(direct, media_hub, monkeypatch):
    selected, payload = direct
    real_insert = soma_hub.insert_rows
    interrupted = False

    def insert(table, rows):
        nonlocal interrupted
        result = real_insert(table, rows)
        if table == "tasks" and not interrupted:
            interrupted = True
            raise requests.Timeout("Committed task but response lost")
        return result

    monkeypatch.setattr("core.soma_hub.insert_rows", insert)
    store = {}
    with pytest.raises(requests.Timeout):
        pipeline.run(payload, store)
    (task,) = media_hub.rows["tasks"].values()
    task["status"] = "Completed"
    selected.config["workflow"]["capture_mode"] = "recognition"
    pipeline.run(payload, store)
    assert len(media_hub.rows["tasks"]) == 1
    assert task["status"] == "Completed"
    assert len(media_hub.rows["synapse_executions"]) == 1


def test_existing_recognition_journal_does_not_gain_a_direct_task(direct, monkeypatch):
    _, payload = direct
    store = {}
    with capture_scope(store, payload) as active:
        active.journal.checkpoint(
            "context",
            lambda: {
                "projects": [],
                "project_ids": {},
                "inventory": {},
            },
        )
        active.journal.checkpoint("parsed_items", lambda: [])
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: type("Settings", (), {"gemini_api_key": "fake"})()
    )
    pipeline.run(payload, store)
    assert next(v for k, v in store.items() if k.startswith("capture:"))["completed"]


def test_invalid_capture_mode_fails_before_writes(direct, media_hub):
    selected, payload = direct
    selected.config["workflow"]["capture_mode"] = "typo"
    with pytest.raises(ValueError, match="capture_mode"):
        pipeline.run(payload, {})
    assert not any(media_hub.rows.values())


@pytest.mark.parametrize(
    "text,url",
    [
        ("https://github.com/example/parser", "https://github.com/example/parser"),
        (" www.example.com ", "https://www.example.com"),
        ("example.com/a?email=a@b&price=$10", "https://example.com/a?email=a@b&price=$10"),
    ],
)
def test_link_only_capture_saves_bookmark_without_task(direct, media_hub, monkeypatch, text, url):
    _, payload = direct
    payload = {**payload, "raw_text": text}
    monkeypatch.setattr(pipeline, "enrich_context", lambda *args: "HTML Title: Example parser")
    monkeypatch.setattr(
        pipeline,
        "generate_with_retry",
        lambda **kwargs: make_gemini_response(
            {
                "Title": "Example parser",
                "Description": "A parser",
                "Tags": [],
                "URL": "https://wrong.example.com",
            }
        ),
    )
    store = {}
    pipeline.run(payload, store)
    pipeline.run(payload, store)
    (bookmark,) = media_hub.rows["bookmarks"].values()
    assert bookmark["url"] == url
    assert bookmark["title"] == "Example parser"
    assert bookmark["description"] == "A parser"
    if "github.com" in url:
        assert bookmark["tags"] == ["Github"]
    assert not media_hub.rows.get("tasks")
    (execution,) = media_hub.rows["synapse_executions"].values()
    assert execution["created_item"] == f"bookmarks/{bookmark['id']}"
    assert execution["code_execution"] == "Success"
    assert execution["raw_input"] == text


@pytest.mark.parametrize(
    "text",
    [
        "Review https://github.com/example/parser and implement this feature",
        "https://example.com $ agent",
        "https://example.com @ https://another.example.com",
        "https://youtu.be/abcdefghijk",
        "https://open.spotify.com/episode/example",
        "https://maps.app.goo.gl/example",
    ],
)
def test_actions_batches_and_special_media_links_stay_tasks(direct, media_hub, text):
    _, payload = direct
    pipeline.run({**payload, "raw_text": text}, {})
    (task,) = media_hub.rows["tasks"].values()
    assert task["notes"] == text
    assert task["dispatch"] == "Requested"
    assert not media_hub.rows.get("bookmarks")


def test_bookmark_exception_respects_disabled_category(direct, media_hub):
    selected, payload = direct
    del selected.config["categories"]["bookmarks"]
    pipeline.run({**payload, "raw_text": "https://example.com"}, {})
    assert len(media_hub.rows["tasks"]) == 1
    assert not media_hub.rows.get("bookmarks")


def test_pending_url_task_keeps_original_dispatch_route(direct, media_hub):
    _, payload = direct
    payload = {**payload, "raw_text": "https://example.com"}
    store = {}
    with capture_scope(store, payload) as active:
        active.journal.checkpoint("capture_mode", lambda: "dispatch")
        active.journal.checkpoint(
            "direct_task",
            lambda: {
                "Name": "https://example.com",
                "Notes": "https://example.com",
                "Status": "To Do",
                "Priority": "High",
                "Due Date": "2026-11-01",
                "Tags": ["Chore"],
                "Lane": "AI",
                "Dispatch": "Requested",
            },
        )
    pipeline.run(payload, store)
    (task,) = media_hub.rows["tasks"].values()
    assert task["notes"] == "https://example.com"
    assert task["dispatch"] == "Requested"
    assert task["due_date"] == "2026-11-01"
    assert not media_hub.rows.get("bookmarks")


def test_unavailable_bookmark_metadata_preserves_existing_title_without_agent(
    direct, media_hub, monkeypatch
):
    _, payload = direct
    url = "https://example.com"
    media_hub.rows["bookmarks"] = {
        "known": {
            "id": "known",
            "url": url,
            "title": "User title",
            "description": "User summary",
            "tags": ["Tech"],
            "updated_at": "2026-01-01T00:00:00.000Z",
            "hub_at": "2026-01-01T00:00:00.001Z",
        }
    }
    monkeypatch.setattr(pipeline, "enrich_context", lambda *args: "Error fetching metadata")
    monkeypatch.setattr(
        pipeline,
        "generate_with_retry",
        lambda **kwargs: make_gemini_response(
            {
                "Title": "Guessed title",
                "Description": "Guessed summary",
                "Tags": [],
                "URL": url,
            }
        ),
    )
    pipeline.run({**payload, "raw_text": url}, {})
    (bookmark,) = media_hub.rows["bookmarks"].values()
    assert bookmark["title"] == "User title"
    assert bookmark["description"] == "User summary"
    assert bookmark["tags"] == ["Tech"]
    assert not media_hub.rows.get("tasks")
