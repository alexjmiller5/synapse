"""Runtime-mapped workflow outputs, journaled before the checked insert boundary.

The caller supplies a persisted capture identity and a stable item/role path.
One serialized worker owns writes to this journal. The store is the existing
Synapse operational store, never a Life Data user table. Remote insert preserves
any existing ID, including a reviewed, completed or tombstoned row.
"""

import contextlib
import copy
import hashlib
import json
import re
from contextvars import ContextVar
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from core import life_hub
from core.workspace import current

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESERVED = {"id", "created_at", "updated_at", "hub_at", "deleted_at"}


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid workflow table or column")
    return value


def _identity(value):
    if not isinstance(value, str) or not value:
        raise ValueError("A persisted capture identity and output role are required")
    return value


def binding_for(kind):
    active = _ACTIVE.get()
    config = active.bindings if active is not None else current().databases.get("workflow", {})
    if not isinstance(config, dict):
        raise ValueError("Invalid workflow configuration")
    if kind not in config:
        return None
    binding = config[kind]
    if not isinstance(binding, dict) or not binding:
        raise ValueError("Invalid selected workflow binding")
    return binding


_ACTIVE = ContextVar("workflow_capture", default=None)


class WorkflowWriter:
    def __init__(self, store, workspace_id, operation_id, *, send=None):
        self.store = store
        self.workspace_id = _identity(workspace_id)
        self.operation_id = _identity(operation_id)
        self.send = send or life_hub.insert_rows

    def create(self, binding, role, values):
        """Freeze first intent, then deliver it without ever upserting a retry.

        `send` must be the checked insert transport (or an equivalent test seam).
        A failed/ambiguous write stays pending. A failed receipt persistence can
        safely resend the same frozen row because insert preserves existing IDs.
        Configuration and AI output changes cannot retarget a retained intent.
        """
        identity = json.dumps(
            ["synapse-workflow-v1", self.workspace_id, self.operation_id, _identity(role)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        key = "workflow:" + hashlib.sha256(identity.encode()).hexdigest()
        record = copy.deepcopy(self.store.get(key))
        if record is None:
            table = _identifier(binding["table"])
            columns = binding["columns"]
            if not isinstance(columns, dict) or not columns:
                raise ValueError("Workflow column mapping is required")
            mapped = [_identifier(column) for column in columns.values()]
            if set(mapped) & _RESERVED or len(set(mapped)) != len(mapped):
                raise ValueError(
                    "Workflow mappings cannot replace identity/clocks or alias columns"
                )
            defaults = binding.get("defaults", {})
            if not isinstance(defaults, dict):
                raise ValueError("Workflow defaults must be a mapping")
            values = {**copy.deepcopy(defaults), **values}
            if unknown := set(values) - set(columns):
                raise ValueError(f"Unmapped workflow properties: {sorted(unknown)}")
            row = {columns[name]: copy.deepcopy(value) for name, value in values.items()}
            row["id"] = uuid5(NAMESPACE_URL, identity).hex
            record = {"identity": identity, "table": table, "row": row, "delivered": False}
            # Refuse non-JSON data before touching either the store or the hub.
            json.dumps(record, ensure_ascii=False, allow_nan=False)
            self.store[key] = copy.deepcopy(record)
        if (
            record.get("identity") != identity
            or not isinstance(record.get("row"), dict)
            or not isinstance(record.get("delivered"), bool)
            or record["row"].get("id") != uuid5(NAMESPACE_URL, identity).hex
        ):
            raise ValueError("Invalid retained workflow intent")
        table = _identifier(record["table"])
        if not record["delivered"]:
            self.send(table, [copy.deepcopy(record["row"])])
            record["delivered"] = True
            self.store[key] = record
        return f"{table}/{record['row']['id']}"


def active_projects(binding, *, pull=None):
    """Keep the existing prompt-list/id-map interface without a partial scan."""
    table = _identifier(binding["table"])
    title = _identifier(binding["title_column"])
    status = _identifier(binding["status_column"])
    allowed = binding["active_statuses"]
    if not isinstance(allowed, list) or not allowed or any(not isinstance(x, str) for x in allowed):
        raise ValueError("Active project status labels are required")
    rows = (pull or life_hub.pull_rows)(table, [title, status])
    names, ids = [], {}
    for row in rows:
        if row.get("deleted_at") or row.get(status) not in allowed:
            continue
        name = row.get(title)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Active project has no title")
        if name in ids and ids[name] != row["id"]:
            raise ValueError("Ambiguous active project title")
        if name not in ids:
            names.append(name)
        ids[name] = row["id"]
    return names, ids


def accepted_capture(payload, workspace_id, *, require_identity=False):
    """The durable queue payload carries identity across worker retries.

    Clients preserve capture_id across HTTP retries. Legacy clients may omit
    it, but cannot obtain HTTP retry deduplication from a text hash on the new
    workflow path. Authentication supplies workspace_id; body workspace is
    deliberately ignored here.
    """
    if require_identity and "capture_id" not in payload:
        raise ValueError("capture_id is required for workflow capture retries")
    capture_id = payload["capture_id"] if "capture_id" in payload else str(uuid4())
    try:
        if not isinstance(capture_id, str) or str(UUID(capture_id)) != capture_id:
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("capture_id must be a canonical UUID") from None
    return {
        "raw_text": payload["raw_text"],
        "source": payload.get("source"),
        "workspace": _identity(workspace_id),
        "capture_id": capture_id,
    }


class CaptureJournal:
    """Checkpoint nondeterministic work under the serialized worker's ownership.

    Persist each parser/classifier/extractor result before dispatching output.
    A capture ID reused with different input is an error, never a fresh capture.
    """

    def __init__(self, store, payload):
        accepted = accepted_capture(payload, payload["workspace"])
        if "capture_id" not in payload:
            raise ValueError("Capture identity must already be durable at acceptance")
        self.store = store
        identity = json.dumps(
            [accepted["workspace"], accepted["capture_id"]], separators=(",", ":")
        )
        self.key = "capture:" + hashlib.sha256(identity.encode()).hexdigest()
        record = self.store.get(self.key)
        if record is None:
            record = {"input": accepted, "checkpoints": {}, "completed": False}
            self._save(record)
        if record.get("input") != accepted:
            raise ValueError("Capture identity already has a different payload")
        if not isinstance(record.get("checkpoints"), dict) or not isinstance(
            record.get("completed"), bool
        ):
            raise ValueError("Invalid capture journal")

    def _save(self, record):
        json.dumps(record, ensure_ascii=False, allow_nan=False)
        self.store[self.key] = copy.deepcopy(record)

    @property
    def completed(self):
        return self.store[self.key]["completed"]

    def checkpoint(self, name, prepare):
        record = copy.deepcopy(self.store[self.key])
        checkpoints = record["checkpoints"]
        if name not in checkpoints:
            if record["completed"]:
                raise ValueError("Completed captures cannot acquire new side effects")
            checkpoints[_identity(name)] = prepare()
            self._save(record)
        return copy.deepcopy(checkpoints[name])

    def finish(self):
        record = copy.deepcopy(self.store[self.key])
        record["completed"] = True
        self._save(record)


@contextlib.contextmanager
def capture_scope(store, payload):
    journal = CaptureJournal(store, payload)
    bindings = journal.checkpoint("bindings", lambda: current().databases.get("workflow", {}))
    active = SimpleNamespace(
        journal=journal,
        bindings=bindings,
        writer=WorkflowWriter(store, current().id, payload["capture_id"]),
        item_index=0,
        counters={},
    )
    token = _ACTIVE.set(active)
    try:
        yield active
    finally:
        _ACTIVE.reset(token)


def current_capture():
    return _ACTIVE.get()


def prepare_item(prepare):
    active = current_capture()
    if active is None:
        return prepare()
    return active.journal.checkpoint(f"item/{active.item_index}/prepared", prepare)


def create_task(data, project_id=None, *, role="task"):
    binding = binding_for("tasks")
    if binding is None:
        raise ValueError("Life Data tasks are not configured")
    active = current_capture()
    if active is None:
        raise ValueError("Workflow tasks require a durable capture context")
    values = {
        name: copy.deepcopy(value) for name, value in data.items() if name in binding["columns"]
    }
    if isinstance(values.get("Links"), list):
        values["Links"] = "\n".join(values["Links"])
    if project_id is not None:
        values["Project"] = [project_id]
    count = active.counters.get(role, 0)
    active.counters[role] = count + 1
    return active.writer.create(binding, f"item/{active.item_index}/{role}/{count}", values)


def log_execution(
    raw_text,
    category,
    status,
    details="",
    created_url=None,
    ai_data=None,
    project_append=False,
    source=None,
):
    binding = binding_for("executions")
    active = current_capture()
    if binding is None or active is None:
        raise ValueError("Workflow executions require configuration and a durable capture context")
    values = {
        "Raw Input": raw_text,
        "Category": category,
        "Code Execution": status,
        "Error Details": str(details),
        "AI Summary": json.dumps(ai_data, ensure_ascii=False, indent=2)
        if ai_data is not None
        else "",
    }
    if created_url is not None:
        values["Created Item"] = created_url
    if project_append:
        values["Tags"] = ["project-append"]
    if source is not None:
        values["Source"] = source
    return active.writer.create(binding, f"item/{active.item_index}/execution", values)
