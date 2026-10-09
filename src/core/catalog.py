"""The Soma catalog slice Synapse builds its extraction prompts and schemas from.

Soma's catalog ENFORCES each column's contract (type, required, select options
and their meanings, defaults, invariants); Synapse only interprets text into
it. `GET /v1/catalog` returns the whole estate, so the workspace keeps the
slice for the tables its config names, cached in the operational store
(`catalog:<workspace>`): reread after TTL_S with ETag revalidation, dropped
when the hub rejects a write (the contract may have changed), and served stale
when the hub is unreachable.
"""

import json
import time

import requests
from requests import RequestException

from core.workspace import current

TTL_S = 3600


def _tables(config):
    tables = {stanza.get("table") for stanza in config.get("categories", {}).values()}
    tables |= {
        b.get("table") for b in (config.get("workflow") or {}).values() if isinstance(b, dict)
    }
    return sorted(t for t in tables if isinstance(t, str))


def _options(raw):
    options = json.loads(raw) if isinstance(raw, str) else raw or []
    return [{"v": o} if isinstance(o, str) else {"v": o["v"], "d": o.get("d")} for o in options]


def _slice(raw, tables):
    out = {table: {"columns": {}, "rules": []} for table in tables}
    for prop in raw.get("properties", []):
        if prop.get("tbl") in out and not prop.get("deleted_at"):
            out[prop["tbl"]]["columns"][prop["col"]] = {
                "type": prop.get("type"),
                "required": bool(prop.get("required")),
                "default": prop.get("default_value"),
                "options": _options(prop.get("options")),
            }
    for rule in raw.get("rules", []):
        if (
            rule.get("tbl") in out
            and rule.get("kind") == "invariant"
            and rule.get("enforce")
            and rule.get("text")
            and not rule.get("deleted_at")
        ):
            out[rule["tbl"]]["rules"].append(rule["text"])
    return out


def _fetch(secrets, etag):
    """(etag, catalog JSON), or None when the hub answers 304 Not Modified."""
    url, token = secrets.get("soma_hub_url"), secrets.get("soma_hub_token")
    if not url or not token:
        raise RuntimeError("SOMA_HUB_URL / SOMA_HUB_TOKEN are not configured")
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "synapse"}
    if etag:
        headers["If-None-Match"] = etag
    response = requests.get(f"{url.rstrip('/')}/v1/catalog", headers=headers, timeout=30)
    if response.status_code == 304:
        return None
    response.raise_for_status()
    return response.headers.get("ETag"), response.json()


def load(ws):
    tables = _tables(ws.config)
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
        fetched = _fetch(ws.secrets, cached and cached["etag"])
    except RequestException:
        if not cached:
            raise
        print("⚠️ Soma catalog unreachable - using the cached copy")
        ws.catalog_memo = cached
        return cached["catalog"]
    etag, raw = fetched or (cached["etag"], None)
    record = {
        "tables": tables,
        "etag": etag,
        "fetched_at": now,
        "catalog": cached["catalog"] if raw is None else _slice(raw, tables),
    }
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
