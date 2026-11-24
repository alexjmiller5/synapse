"""Device-bound, operator-verified filing receipts in the serialized owned store."""

from copy import deepcopy
from hashlib import sha256
import json
import re
from uuid import NAMESPACE_URL, uuid5

from core import capture_clients, soma_hub, workspace
from core.workflow import accepted_capture


def _identity(workspace_id, capture_id):
    accepted_capture({"capture_id": capture_id, "raw_text": ""}, workspace_id)
    return sha256(
        json.dumps([workspace_id, capture_id], separators=(",", ":")).encode()
    ).hexdigest()


def _client(store, client_id, workspace_id):
    client = store.get(f"client:{client_id}")
    if (
        not client
        or client.get("purpose")
        or client.get("workspace", workspace.DEFAULT_ID) != workspace_id
    ):
        raise ValueError("Invalid capture client")
    return client


def bind_client(store, client_id, payload):
    accepted = accepted_capture(payload, payload["workspace"])
    _client(store, client_id, accepted["workspace"])
    key = "capture-owner:" + _identity(accepted["workspace"], accepted["capture_id"])
    value = {
        "client_id": client_id,
        "source_digest": sha256(accepted["raw_text"].encode()).hexdigest(),
    }
    previous = store.get(key)
    if previous is not None and previous != value:
        raise ValueError("Capture ownership or payload differs")
    if previous is None:
        store[key] = value


def receipt(store, client, capture_id):
    identity = _identity(client["workspace"], capture_id)
    owner = store.get("capture-owner:" + identity)
    if not owner or owner["client_id"] != client["client_id"]:
        raise capture_clients.Unauthorized()
    result = store.get("filing-result:" + identity)
    if result is not None:
        return deepcopy(result)
    journal = store.get("capture:" + identity)
    return {
        "capture_id": capture_id,
        "source_digest": owner["source_digest"],
        "state": "captured" if journal and journal["completed"] else "received",
    }


def _row_id(workspace_id, capture_id, role):
    value = json.dumps(
        ["synapse-workflow-v1", workspace_id, capture_id, role],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return uuid5(NAMESPACE_URL, value).hex


def finalize(store, request):
    """Trusted operator attests filing; service independently checks durable source/targets.

    Called only by the operator's serialized worker operation, never a device request.
    An older capture may be explicitly bound to its verified device during finalization.
    """
    if (
        not isinstance(request, dict)
        or not {"capture_id", "actor", "items"}.issubset(request)
        or set(request) - {"capture_id", "client_id", "actor", "items"}
    ):
        raise ValueError("Invalid filing request")
    wid = workspace.current().id
    capture_id = request["capture_id"]
    identity = _identity(wid, capture_id)
    owner = store.get("capture-owner:" + identity)
    client_id = request.get("client_id") or (owner or {}).get("client_id")
    _client(store, client_id, wid)
    if not isinstance(request["actor"], str) or not request["actor"].strip():
        raise ValueError("A filing operator identity is required")
    items = request["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Filed references are required")
    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != {"table", "id"}
            or not isinstance(item["table"], str)
            or not re.fullmatch(r"[A-Za-z_]\w*", item["table"])
            or not isinstance(item["id"], str)
            or not item["id"].strip()
        ):
            raise ValueError("Invalid filed reference")
    journal = store.get("capture:" + identity)
    if not journal or not journal["completed"] or "direct_task" not in journal["checkpoints"]:
        raise ValueError("No completed direct capture")
    payload = journal["input"]
    if owner and owner["client_id"] != client_id:
        raise ValueError("Capture belongs to another device")
    key = "filing-result:" + identity
    result = {
        "capture_id": capture_id,
        "source_digest": sha256(payload["raw_text"].encode()).hexdigest(),
        "state": "filed",
        "actor": request["actor"],
        "items": deepcopy(items),
    }
    previous = store.get(key)
    if previous is not None:
        if previous != result:
            raise ValueError("Filing receipt is immutable")
        return deepcopy(previous)
    bindings = journal["checkpoints"]["bindings"]
    task_binding, log_binding = bindings["tasks"], bindings["executions"]
    task_id = _row_id(wid, capture_id, "item/0/task/0")
    log_id = _row_id(wid, capture_id, "item/0/execution")
    task = soma_hub.read_row(task_binding["table"], task_id, [task_binding["columns"]["Notes"]])
    log = soma_hub.read_row(
        log_binding["table"],
        log_id,
        [
            log_binding["columns"][field]
            for field in ("Raw Input", "Code Execution", "Created Item")
        ],
    )
    if (
        not task
        or task.get("deleted_at")
        or not log
        or log.get("deleted_at")
        or payload["raw_text"] not in (task.get(task_binding["columns"]["Notes"]) or "")
        or log.get(log_binding["columns"]["Raw Input"]) != payload["raw_text"]
        or log.get(log_binding["columns"]["Code Execution"]) != "Success"
        or log.get(log_binding["columns"]["Created Item"]) != f"{task_binding['table']}/{task_id}"
    ):
        raise ValueError("Durable source verification failed")
    for item in items:
        target = soma_hub.read_row(item["table"], item["id"], [])
        if not target or target.get("deleted_at"):
            raise ValueError("Filed target is not live")
    bind_client(store, client_id, payload)
    store[key] = result
    return deepcopy(result)
