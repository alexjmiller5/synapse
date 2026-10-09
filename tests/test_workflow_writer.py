"""Durable output boundaries use synthetic workspace configuration and rows."""

import copy
from unittest.mock import Mock

import pytest
import requests

from core.workflow import WorkflowWriter, active_projects


BINDING = {
    "table": "work_items",
    "columns": {"Name": "title", "Notes": "notes", "Project": "project_ids"},
}


def writer(store, send, operation="capture-1"):
    return WorkflowWriter(store, "workspace-a", operation, send=send)


def test_timeout_after_commit_replays_identical_id_and_body_without_overwrite():
    store, remote = {}, {}

    def send(table, rows):
        row = rows[0]
        if row["id"] not in remote:
            remote[row["id"]] = copy.deepcopy(row)
            raise requests.Timeout("response lost after commit")
        return {"inserted": [], "existing": [row["id"]], "rejected": []}

    first = writer(store, send)
    with pytest.raises(requests.Timeout):
        first.create(BINDING, "item/0/task", {"Name": "Original", "Notes": "Full notes"})
    task_id = next(iter(remote))
    remote[task_id].update(title="Human edit", deleted_at="2026-01-01T00:00:00.000Z")
    second = writer(store, send)
    assert (
        second.create(BINDING, "item/0/task", {"Name": "Re-extracted"}) == f"work_items/{task_id}"
    )
    assert len(remote) == 1
    assert remote[task_id]["title"] == "Human edit"
    assert remote[task_id]["deleted_at"] is not None


def test_distinct_operations_and_output_roles_have_distinct_ids():
    send = Mock(return_value={})
    refs = {
        writer({}, send, op).create(BINDING, role, {"Name": "Same text"})
        for op in ["one", "two"]
        for role in ["item/0/task", "item/1/task", "item/0/error-task"]
    }
    assert len(refs) == 6


def test_prepare_is_durable_before_network_and_freezes_mapping_and_payload():
    store, calls = {}, []

    def send(table, rows):
        calls.append((table, copy.deepcopy(rows)))
        assert store
        raise requests.Timeout()

    a = writer(store, send)
    with pytest.raises(requests.Timeout):
        a.create(BINDING, "item/0/task", {"Name": "Original", "Project": ["project-1"]})
    changed = {"table": "new_table", "columns": {"Name": "renamed"}}
    with pytest.raises(requests.Timeout):
        writer(store, send).create(changed, "item/0/task", {"Name": "New"})
    assert calls[0] == calls[1]
    assert calls[0][1][0]["project_ids"] == ["project-1"]


def test_storage_failure_has_no_remote_side_effect():
    class Broken(dict):
        def __setitem__(self, key, value):
            raise OSError("disk full")

    send = Mock()
    with pytest.raises(OSError):
        writer(Broken(), send).create(BINDING, "item/0/task", {"Name": "One"})
    send.assert_not_called()


def test_json_and_unicode_are_never_truncated():
    send = Mock(return_value={})
    body = "🧪長い" * 5000
    writer({}, send).create(BINDING, "log", {"Name": body, "Notes": {"nested": body}})
    row = send.call_args.args[1][0]
    assert row["title"] == body
    assert row["notes"] == {"nested": body}


@pytest.mark.parametrize("columns", [{"Name": "id"}, {"Name": "updated_at"}, {"A": "x", "B": "x"}])
def test_invalid_mapping_fails_before_storage_or_network(columns):
    store, send = {}, Mock()
    with pytest.raises(ValueError):
        writer(store, send).create({"table": "items", "columns": columns}, "task", {"Name": "A"})
    assert store == {}
    send.assert_not_called()


def test_missing_mapping_for_nonempty_values_fails_instead_of_losing_data():
    store, send = {}, Mock()
    with pytest.raises(ValueError, match="Unmapped"):
        writer(store, send).create(BINDING, "task", {"Name": "A", "Tags": ["One"]})
    assert store == {}
    send.assert_not_called()


def test_project_lookup_uses_configured_labels_and_excludes_inactive_deleted():
    pull = Mock(
        return_value=[
            {"id": "p1", "name": "Alpha", "phase": "Open", "deleted_at": None},
            {"id": "p2", "name": "Beta", "phase": "Done", "deleted_at": None},
            {"id": "p3", "name": "Gone", "phase": "Open", "deleted_at": "stamp"},
            {"id": "p4", "name": "Gamma", "phase": "Started", "deleted_at": None},
        ]
    )
    config = {
        "table": "initiatives",
        "title_column": "name",
        "status_column": "phase",
        "active_statuses": ["Open", "Started"],
    }
    assert active_projects(config, pull=pull) == (
        ["Alpha", "Gamma"],
        {"Alpha": "p1", "Gamma": "p4"},
    )
    pull.assert_called_once_with("initiatives", ["name", "phase"])


def test_ambiguous_project_names_fail_instead_of_linking_arbitrary_project():
    pull = Mock(return_value=[{"id": x, "title": "Same", "status": "Open"} for x in ["a", "b"]])
    config = {
        "table": "initiatives",
        "title_column": "title",
        "status_column": "status",
        "active_statuses": ["Open"],
    }
    with pytest.raises(ValueError, match="Ambiguous"):
        active_projects(config, pull=pull)


def test_project_backend_is_selected_from_workspace_without_notion(monkeypatch):
    from core import business_logic, workspace

    ws = workspace.build(
        "sample",
        {
            "workflow": {
                "projects": {
                    "table": "initiatives",
                    "title_column": "name",
                    "status_column": "phase",
                    "active_statuses": ["Open"],
                }
            }
        },
    )
    pull = Mock(return_value=[{"id": "a", "name": "Alpha", "phase": "Open"}])
    notion = Mock(side_effect=AssertionError("Notion must not be queried"))
    monkeypatch.setattr("core.soma_hub.pull_rows", pull)
    monkeypatch.setattr(business_logic, "query_notion_db", notion)
    with workspace.use(ws):
        assert business_logic.fetch_active_projects() == (["Alpha"], {"Alpha": "a"})
    notion.assert_not_called()


def test_invalid_selected_project_backend_does_not_fall_back(monkeypatch):
    from core import business_logic, workspace

    ws = workspace.build("sample", {"workflow": {"projects": {}}})
    notion = Mock(side_effect=AssertionError("No fallback"))
    monkeypatch.setattr(business_logic, "query_notion_db", notion)
    with workspace.use(ws), pytest.raises(ValueError):
        business_logic.fetch_active_projects()
    notion.assert_not_called()


def test_delivered_output_is_not_sent_again_after_human_review():
    store, send = {}, Mock(return_value={})
    original = writer(store, send).create(BINDING, "execution", {"Name": "Original"})
    assert writer(store, send).create(BINDING, "execution", {"Name": "Different"}) == original
    assert send.call_count == 1


def test_rejected_write_remains_pending_and_does_not_claim_completion():
    store, send = {}, Mock(side_effect=RuntimeError("rejected"))
    for _ in range(2):
        with pytest.raises(RuntimeError, match="rejected"):
            writer(store, send).create(BINDING, "task", {"Name": "Original"})
    assert all(not value["delivered"] for value in store.values())
    assert send.call_args_list[0] == send.call_args_list[1]


def test_same_capture_id_in_different_workspaces_does_not_collide():
    store, send = {}, Mock(return_value={})
    a = WorkflowWriter(store, "a", "same", send=send).create(BINDING, "task", {"Name": "A"})
    b = WorkflowWriter(store, "b", "same", send=send).create(BINDING, "task", {"Name": "A"})
    assert a != b


def test_retained_intent_is_not_mutated_by_a_transport():
    store = {}

    def send(table, rows):
        rows[0]["title"] = "Mutated"
        raise RuntimeError("failed")

    with pytest.raises(RuntimeError):
        writer(store, send).create(BINDING, "task", {"Name": "Original"})
    assert next(iter(store.values()))["row"]["title"] == "Original"


def test_runtime_defaults_fill_missing_values_without_overriding_explicit_values():
    send = Mock(return_value={})
    binding = {**BINDING, "defaults": {"Name": "Default", "Notes": "Default notes"}}
    writer({}, send).create(binding, "task", {"Name": "Explicit"})
    assert send.call_args.args[1][0]["title"] == "Explicit"
    assert send.call_args.args[1][0]["notes"] == "Default notes"
