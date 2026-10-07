"""Runtime-mapped workflow outputs, journaled before the checked insert boundary.

The caller supplies a persisted capture identity and a stable item/role path.
One serialized worker owns writes to this journal. The store is the existing
Synapse operational store, never a Life Data user table. Remote insert preserves
any existing ID, including a reviewed, completed or tombstoned row.
"""

import copy
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

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
    config = current().databases.get("workflow", {})
    if not isinstance(config, dict):
        raise ValueError("Invalid workflow configuration")
    if kind not in config:
        return None
    binding = config[kind]
    if not isinstance(binding, dict) or not binding:
        raise ValueError("Invalid selected workflow binding")
    return binding


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
