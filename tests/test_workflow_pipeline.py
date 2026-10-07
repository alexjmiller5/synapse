"""Capture-to-task-to-execution retry tests at the external write boundary."""

import copy
from unittest.mock import Mock

import pytest
import requests

from core import pipeline, workspace
from helpers import make_gemini_response


CAPTURE = "e8bfc1e9-6f0e-4c15-8764-7c98d4b3a2ab"


@pytest.fixture
def workflow(monkeypatch):
    base = copy.deepcopy(workspace.current())
    base.databases["workflow"] = {
        "tasks": {
            "table": "work_items",
            "columns": {
                "Name": "title",
                "Status": "status",
                "Priority": "priority",
                "Due Date": "due",
                "Tags": "tags",
                "Notes": "notes",
                "Project": "project_ids",
            },
        },
        "executions": {
            "table": "capture_logs",
            "columns": {
                "Raw Input": "raw_input",
                "Category": "category",
                "Code Execution": "code_execution",
                "Error Details": "error_details",
                "AI Summary": "ai_summary",
                "Created Item": "created_item",
                "Tags": "tags",
                "Source": "source",
            },
        },
    }
    monkeypatch.setattr(pipeline, "fetch_active_projects", Mock(return_value=([], {})))
    monkeypatch.setattr(pipeline, "fetch_inventory_map", Mock(return_value={}))
    monkeypatch.setattr(
        pipeline,
        "parse_raw_input",
        Mock(return_value=[{"core_text": "Do a thing", "context_notes": "task"}]),
    )
    monkeypatch.setattr(pipeline, "hydrate_dynamic_options", Mock())
    with workspace.use(base):
        yield base


def test_logging_timeout_restarts_with_same_task_and_execution_and_no_reparse(
    workflow, monkeypatch, mock_gemini, mock_notion
):
    remote, store = {}, {}

    def send(table, rows):
        row = copy.deepcopy(rows[0])
        key = (table, row["id"])
        created = key not in remote
        remote.setdefault(key, row)
        if table == "capture_logs" and created:
            raise requests.Timeout("committed execution, lost response")
        return {
            "inserted": [row["id"]] if created else [],
            "existing": [] if created else [row["id"]],
            "rejected": [],
        }

    monkeypatch.setattr("core.life_hub.insert_rows", send)
    mock_gemini.models.generate_content.side_effect = [
        make_gemini_response({"Name": "Do a thing", "Tags": ["Chore"], "Due Date": "2026-01-01"})
    ]
    payload = {
        "raw_text": "Do a thing",
        "source": "test-client",
        "workspace": workflow.id,
        "capture_id": CAPTURE,
    }
    with pytest.raises(requests.Timeout):
        pipeline.run(payload, store=store)
    assert not next(value for key, value in store.items() if key.startswith("capture:"))[
        "completed"
    ]
    task = next(row for (table, _), row in remote.items() if table == "work_items")
    task["status"] = "Completed"
    pipeline.run(payload, store=store)
    pipeline.run(payload, store=store)
    assert len(remote) == 2
    assert task["status"] == "Completed"
    execution = next(row for (table, _), row in remote.items() if table == "capture_logs")
    assert execution["created_item"] == f"work_items/{task['id']}"
    assert execution["source"] == "test-client"
    assert pipeline.parse_raw_input.call_count == 1
    assert mock_gemini.models.generate_content.call_count == 1
    mock_notion.pages.create.assert_not_called()


def test_missing_durable_identity_or_store_fails_before_any_capture_effect(
    workflow, mock_gemini, mock_notion
):
    with pytest.raises(ValueError):
        pipeline.run({"raw_text": "One"})
    assert not mock_gemini.models.generate_content.called
    mock_notion.pages.create.assert_not_called()


def test_missing_model_config_does_not_mark_capture_complete(workflow, monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "get_settings",
        Mock(return_value=type("Settings", (), {"gemini_api_key": None})()),
    )
    store = {}
    with pytest.raises(RuntimeError):
        pipeline.run(
            {"raw_text": "One", "workspace": workflow.id, "capture_id": CAPTURE}, store=store
        )
    assert all(not value.get("completed", False) for value in store.values())


def test_project_context_is_frozen_across_restart(workflow, monkeypatch, mock_gemini):
    pipeline.fetch_active_projects.return_value = (["Alpha"], {"Alpha": "project-a"})
    pipeline.parse_raw_input.return_value = [{"core_text": "Alpha task", "context_notes": "task"}]
    mock_gemini.models.generate_content.side_effect = [
        make_gemini_response({"Name": "Alpha task", "Tags": ["Chore"]})
    ]
    sent = []

    def send(table, rows):
        sent.append((table, copy.deepcopy(rows)))
        raise requests.Timeout()

    monkeypatch.setattr("core.life_hub.insert_rows", send)
    store = {}
    payload = {"raw_text": "Alpha task", "workspace": workflow.id, "capture_id": CAPTURE}
    with pytest.raises(requests.Timeout):
        pipeline.run(payload, store=store)
    pipeline.fetch_active_projects.side_effect = AssertionError("Snapshot must be retained")
    with pytest.raises(requests.Timeout):
        pipeline.run(payload, store=store)
    assert sent[0] == sent[1]
    assert sent[0][1][0]["project_ids"] == ["project-a"]


def test_selected_task_hydration_does_not_read_notion(workflow, mock_notion):
    from core.business_logic import hydrate_dynamic_options

    hydrate_dynamic_options(only_category="tasks")
    mock_notion.databases.retrieve.assert_not_called()


def test_cleanup_tasks_follow_selected_backend_and_keep_stable_roles(
    workflow, monkeypatch, mock_notion
):
    from core.notion_utils import create_cleanup_task, create_high_priority_task
    from core.workflow import capture_scope

    send = Mock(return_value={})
    monkeypatch.setattr("core.life_hub.insert_rows", send)
    payload = {"raw_text": "One", "workspace": workflow.id, "capture_id": CAPTURE}
    store = {}
    for _ in range(2):
        with capture_scope(store, payload):
            create_cleanup_task("Review metadata")
            create_high_priority_task("Review capture")
    assert send.call_count == 2
    assert {c.args[1][0]["priority"] for c in send.call_args_list} == {"Low", "High"}
    mock_notion.pages.create.assert_not_called()


def test_identical_text_with_distinct_capture_ids_remains_two_operations(
    workflow, monkeypatch, mock_gemini
):
    send = Mock(return_value={})
    monkeypatch.setattr("core.life_hub.insert_rows", send)
    mock_gemini.models.generate_content.return_value = make_gemini_response(
        {"Name": "Do a thing", "Tags": ["Chore"]}
    )
    store = {}
    seen = {}
    for identity in [CAPTURE, "12a98ba3-7951-48a2-9712-3a1a28d5de02"]:
        pipeline.run(
            {"raw_text": "Do a thing", "workspace": workflow.id, "capture_id": identity},
            seen=seen,
            store=store,
        )
    assert len({call.args[1][0]["id"] for call in send.call_args_list}) == 4
    assert seen == {}


def test_large_unicode_and_ai_metadata_survive_capture_logging(workflow, monkeypatch, mock_gemini):
    body = "長い🧪" * 4000
    pipeline.parse_raw_input.return_value = [{"core_text": body, "context_notes": "task"}]
    mock_gemini.models.generate_content.return_value = make_gemini_response(
        {"Name": body, "Tags": ["Chore"], "Notes": body}
    )
    send = Mock(return_value={})
    monkeypatch.setattr("core.life_hub.insert_rows", send)
    pipeline.run({"raw_text": body, "workspace": workflow.id, "capture_id": CAPTURE}, store={})
    task = next(c.args[1][0] for c in send.call_args_list if c.args[0] == "work_items")
    log = next(c.args[1][0] for c in send.call_args_list if c.args[0] == "capture_logs")
    assert task["title"] == body and task["notes"] == body
    assert log["raw_input"] == body + " (Context: task)"
    assert body in log["ai_summary"]


def test_partial_workflow_switch_is_rejected_before_notion_side_effects(workflow, mock_notion):
    del workflow.databases["workflow"]["tasks"]
    with pytest.raises(ValueError, match="together"):
        pipeline.run({"raw_text": "One", "workspace": workflow.id, "capture_id": CAPTURE}, store={})
    mock_notion.pages.create.assert_not_called()


def test_preparation_failure_freezes_one_actionable_error_across_logging_retry(
    workflow, monkeypatch, mock_gemini
):
    remote, store = {}, {}

    def send(table, rows):
        row = copy.deepcopy(rows[0])
        key = (table, row["id"])
        created = key not in remote
        remote.setdefault(key, row)
        if table == "capture_logs" and created:
            raise requests.Timeout("response lost")
        return {}

    monkeypatch.setattr("core.life_hub.insert_rows", send)
    failure = Mock(side_effect=ValueError("Invalid model output"))
    monkeypatch.setattr(pipeline, "generate_with_retry", failure)
    payload = {"raw_text": "Do a thing", "workspace": workflow.id, "capture_id": CAPTURE}
    with pytest.raises(requests.Timeout):
        pipeline.run(payload, store=store)
    pipeline.run(payload, store=store)
    assert len(remote) == 2
    assert failure.call_count == 1
    task = next(row for (table, _), row in remote.items() if table == "work_items")
    log = next(row for (table, _), row in remote.items() if table == "capture_logs")
    assert task["priority"] == "High"
    assert log["code_execution"] == "Error(s)"
    assert "Invalid model output" in log["error_details"]
    assert log["created_item"] == f"work_items/{task['id']}"
