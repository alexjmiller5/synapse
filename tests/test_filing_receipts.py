"""A device may see only its own operator-verified filing receipts."""

import copy
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import capture_clients, filing, pipeline, workspace

CAPTURE = "45601840-1df2-47e5-9eb3-9175ed2932e3"


@pytest.fixture
def captured(monkeypatch, media_hub):
    selected = copy.deepcopy(workspace.current())
    selected.config["workflow"].pop("capture_mode", None)
    store = {}
    client = capture_clients.issue(store, "Fixture", selected.id)
    payload = {
        "raw_text": "Full original plaintext and HTML",
        "source": "apple-notes",
        "capture_id": CAPTURE,
        "workspace": selected.id,
    }
    with workspace.use(selected):
        filing.bind_client(store, client["client_id"], payload)
        pipeline.run(payload, store)
        task = next(iter(media_hub.rows["tasks"].values()))
        request = {
            "capture_id": CAPTURE,
            "client_id": client["client_id"],
            "actor": "fixture-session",
            "items": [{"table": "tasks", "id": task["id"]}],
        }
        yield store, client, payload, request


def test_filing_receipt_requires_verified_remote_source_and_targets(captured, media_hub):
    store, client, payload, request = captured
    assert filing.receipt(store, client, CAPTURE)["state"] == "captured"
    result = filing.finalize(store, request)
    assert result["state"] == "filed"
    assert result["source_digest"] == hashlib.sha256(payload["raw_text"].encode()).hexdigest()
    assert result["items"] == request["items"]
    assert filing.receipt(store, client, CAPTURE) == result
    assert filing.finalize(store, request) == result


@pytest.mark.parametrize(
    "damage", ["task_source", "execution_source", "execution_failed", "target_deleted"]
)
def test_incomplete_or_changed_source_never_produces_filed_receipt(captured, media_hub, damage):
    store, client, _, request = captured
    task = next(iter(media_hub.rows["tasks"].values()))
    execution = next(iter(media_hub.rows["synapse_executions"].values()))
    if damage == "task_source":
        task["notes"] = "Truncated original"
    elif damage == "execution_source":
        execution["raw_input"] = "Different original"
    elif damage == "execution_failed":
        execution["code_execution"] = "Error(s)"
    else:
        task["deleted_at"] = "2026-01-01T00:00:00.000Z"
    with pytest.raises(ValueError):
        filing.finalize(store, request)
    assert filing.receipt(store, client, CAPTURE)["state"] == "captured"


def test_other_device_workspace_or_unknown_capture_cannot_read_receipt(captured):
    store, client, _, request = captured
    other = capture_clients.issue(store, "Other", client["workspace"])
    with pytest.raises(capture_clients.Unauthorized):
        filing.receipt(store, other, CAPTURE)
    with pytest.raises(capture_clients.Unauthorized):
        filing.receipt(store, {**client, "workspace": "other"}, CAPTURE)
    with pytest.raises(ValueError):
        filing.receipt(store, client, "not-an-id")
    with pytest.raises(ValueError):
        filing.finalize(store, {**request, "client_id": other["client_id"]})


def test_bind_cannot_reassign_a_capture_or_accept_a_different_payload(captured):
    store, client, payload, _ = captured
    other = capture_clients.issue(store, "Other", client["workspace"])
    with pytest.raises(ValueError):
        filing.bind_client(store, other["client_id"], payload)
    with pytest.raises(ValueError):
        filing.bind_client(store, client["client_id"], {**payload, "raw_text": "changed"})


def test_legacy_default_workspace_device_keeps_capture_access(captured):
    store, client, payload, request = captured
    del store[f"client:{client['client_id']}"]["workspace"]
    filing.bind_client(store, client["client_id"], payload)
    assert filing.finalize(store, request)["state"] == "filed"


def test_client_endpoint_receipt_read_does_not_spawn_or_accept_other_devices(captured, monkeypatch):
    store, client, _, request = captured
    filing.finalize(store, request)
    spec = importlib.util.spec_from_file_location(
        "filing_app", Path(__file__).parents[1] / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_state", lambda: store)
    monkeypatch.setattr(
        module, "process", SimpleNamespace(spawn=lambda _: pytest.fail("receipt spawned"))
    )
    endpoint = module.capture.get_raw_f()
    assert (
        endpoint({"action": "receipt", "capture_id": CAPTURE}, "Bearer " + client["token"])["state"]
        == "filed"
    )
    other = capture_clients.issue(store, "Other", client["workspace"])
    assert (
        endpoint(
            {"action": "receipt", "capture_id": CAPTURE}, "Bearer " + other["token"]
        ).status_code
        == 403
    )


def test_only_filing_operator_can_finalize_and_cannot_capture(captured, monkeypatch):
    store, client, _, request = captured
    operator = capture_clients.issue_filing_operator(store, "Filing fixture", client["workspace"])
    spec = importlib.util.spec_from_file_location(
        "finalize_app", Path(__file__).parents[1] / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_state", lambda: store)
    monkeypatch.setattr(
        module,
        "process",
        SimpleNamespace(
            remote=lambda payload: filing.finalize(store, payload["filing_operation"]),
            spawn=lambda _: pytest.fail("filing operator spawned capture"),
        ),
    )
    endpoint = module.capture.get_raw_f()
    body = {"action": "finalize", "request": request}
    assert endpoint(body, "Bearer " + client["token"]).status_code == 403
    assert endpoint(body, "Bearer " + operator["token"])["state"] == "filed"
    assert endpoint({"raw_text": "work"}, "Bearer " + operator["token"]).status_code == 401
    capture_clients.revoke(store, operator["client_id"])
    assert endpoint(body, "Bearer " + operator["token"]).status_code == 401
