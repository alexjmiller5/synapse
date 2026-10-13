"""The Soma catalog slice Synapse builds its extraction prompts and schemas from.

Soma's catalog ENFORCES each column's contract (type, required, select options
and their meanings, defaults, invariants); Synapse only interprets text into
it. Each table a category writes is read with `POST /v1/catalog/projection`
for exactly the columns its prompt fields map to (the credential holds a
catalog grant per such column): type, required, options, static default, plus
the table's enforced rule texts. A refused projection (a column without a
catalog row or grant) leaves that table without a contract, as an uncataloged
table has none. The slice is cached in the operational store
(`catalog:<workspace>`): reprojected after TTL_S (the route has no ETag) or when
the config maps other columns, dropped when the hub rejects a write (the
contract may have changed), and served stale when the hub is unreachable.
"""

import json
import time

import requests
from requests import RequestException

from core.workspace import current

TTL_S = 3600


def _columns(config):
    """{table: sorted columns} the extraction prompts read: each category's
    property fields through its `columns`/`capture_columns` (tasks: the
    workspace's workflow.tasks binding)."""
    out = {}
    for name, stanza in config.get("categories", {}).items():
        if name == "tasks":
            binding = (config.get("workflow") or {}).get("tasks") or {}
            table, mapping = binding.get("table"), binding.get("columns") or {}
        else:
            table = stanza.get("table")
            mapping = {**(stanza.get("columns") or {}), **(stanza.get("capture_columns") or {})}
        used = {mapping[p] for p in stanza.get("properties") or {} if mapping.get(p)}
        if isinstance(table, str) and used:
            out.setdefault(table, set()).update(used)
    return {table: sorted(cols) for table, cols in sorted(out.items())}


def _options(raw):
    options = json.loads(raw) if isinstance(raw, str) else raw or []
    return [{"v": o} if isinstance(o, str) else {"v": o["v"], "d": o.get("d")} for o in options]


def _project(secrets, table, columns):
    url, token = secrets.get("soma_hub_url"), secrets.get("soma_hub_token")
    if not url or not token:
        raise RuntimeError("SOMA_HUB_URL / SOMA_HUB_TOKEN are not configured")
    response = requests.post(
        f"{url.rstrip('/')}/v1/catalog/projection",
        json={"table": table, "columns": columns},
        headers={"Authorization": f"Bearer {token}", "User-Agent": "synapse"},
        timeout=30,
    )
    if response.status_code == 403:
        # A column without a catalog row or grant: no contract to state for this
        # table (as for an uncataloged one); the hub still validates its writes.
        print(f"⚠️ Soma refused the catalog projection of {table} {columns}")
        return {"columns": {}, "rules": []}
    response.raise_for_status()
    raw = response.json()
    return {
        "columns": {
            prop["column"]: {
                "type": prop.get("type"),
                "required": bool(prop.get("required")),
                "default": prop.get("default"),
                "options": _options(prop.get("options")),
            }
            for prop in raw.get("properties", [])
        },
        "rules": list(raw.get("rules") or []),
    }


def load(ws):
    tables = _columns(ws.config)
    if ws.catalog_memo and ws.catalog_memo["tables"] == tables:
        return ws.catalog_memo["catalog"]
    key = f"catalog:{ws.id}"
    cached = ws.store.get(key) if ws.store is not None else None
    if cached and cached.get("tables") != tables:
        cached = None
    now = time.time()
    if cached and now - cached["fetched_at"] < TTL_S:
        ws.catalog_memo = cached
        return cached["catalog"]
    try:
        fetched = {table: _project(ws.secrets, table, cols) for table, cols in tables.items()}
    except RequestException:
        if not cached:
            raise
        print("⚠️ Soma catalog unavailable - using the cached copy")
        ws.catalog_memo = cached
        return cached["catalog"]
    record = {"tables": tables, "fetched_at": now, "catalog": fetched}
    if ws.store is not None:
        ws.store[key] = record
    ws.catalog_memo = record
    return record["catalog"]


def table(name):
    """One table's contract for the active workspace: {columns: {col: prop}, rules: [text]}."""
    return load(current()).get(name) or {"columns": {}, "rules": []}


def invalidate():
    """The hub rejected a write: reread the catalog on the next load."""
    ws = current()
    ws.catalog_memo = None
    if ws.store is not None:
        ws.store.pop(f"catalog:{ws.id}", None)
