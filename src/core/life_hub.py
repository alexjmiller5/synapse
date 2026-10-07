"""Life Data transport for sparse updates, complete reads and stable-ID creation."""

from datetime import datetime
import hashlib
import re
from types import SimpleNamespace

import requests

from core.workspace import current


def file_key(key):
    if (
        not isinstance(key, str)
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", key)
        or any(part in ("", ".", "..") for part in key.split("/"))
    ):
        raise ValueError("Retained file key must be canonical")
    return key


def retain_text(key, value, *, settings=None, client=None):
    """Create an immutable original through the supported file API, then verify.

    Retrying a lost upload reply accepts an existing object only when its bytes
    match exactly. Never replace another retained object at the same key.
    """
    key = file_key(key)
    settings = settings or _hub()
    if not settings.life_hub_url or not settings.life_hub_token:
        raise RuntimeError("Life Data files require configured workspace auth")
    raw = value.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    path = f"/v1/files/{key}"
    url = settings.life_hub_url.rstrip("/") + path
    headers = {"Authorization": f"Bearer {settings.life_hub_token}", "User-Agent": "synapse"}
    transport = client or requests
    response = transport.put(
        url,
        data=raw,
        headers={
            **headers,
            "If-None-Match": "*",
            "X-Content-SHA256": digest,
            "Content-Type": "text/plain; charset=utf-8",
        },
        timeout=60,
    )
    if response.status_code == 201:
        receipt = response.json()
        if (
            receipt.get("key") != key
            or receipt.get("bytes") != len(raw)
            or receipt.get("sha256") != digest
        ):
            raise RuntimeError("Invalid retained file receipt")
    elif response.status_code != 412:
        response.raise_for_status()
        raise RuntimeError("Unexpected retained file response")
    readback = transport.get(url, headers=headers, timeout=60)
    readback.raise_for_status()
    if readback.content != raw:
        raise RuntimeError("Retained file content does not match the original")
    return path


def _hub():
    """The active workspace's Life Data hub (URL and dedicated token)."""
    settings = current().secrets
    return SimpleNamespace(
        life_hub_url=settings.get("life_hub_url"),
        life_hub_token=settings.get("life_hub_token"),
    )


def _post(path, body, *, settings=None, client=None):
    settings = settings or _hub()
    if not settings.life_hub_url or not settings.life_hub_token:
        raise RuntimeError("LIFE_HUB_URL / LIFE_HUB_TOKEN are not configured")
    response = (client or requests).post(
        f"{settings.life_hub_url.rstrip('/')}{path}",
        json=body,
        headers={
            "Authorization": f"Bearer {settings.life_hub_token}",
            "User-Agent": "synapse",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Life Data returned an invalid response")
    return payload


def push_rows(table, rows, *, settings=None, client=None):
    """Sparse upsert for existing category handlers; callers inspect rejections."""
    return _post(
        "/v1/rows/push",
        {"table": table, "columns": sorted({k for row in rows for k in row}), "rows": rows},
        settings=settings,
        client=client,
    )


def pull_rows(table, columns, *, settings=None, client=None):
    """Exhaust the hub scan before returning non-deleted rows.

    A scan is not a frozen snapshot. Missing/invalid cursors and failed pages
    raise rather than presenting a partial project or deduplication inventory.
    """
    cols = ["id", "deleted_at"] + [c for c in columns if c not in ("id", "deleted_at")]
    body = {"table": table, "columns": cols, "since": "", "limit": 200}
    rows, cursors = {}, set()
    while True:
        page = _post("/v1/rows/pull", dict(body), settings=settings, client=client)
        batch = page.get("rows")
        if "next_cursor" not in page or not isinstance(batch, list):
            raise RuntimeError("Life Data returned an incomplete page or missing cursor")
        if any(not isinstance(row, dict) or not _identity(row.get("id")) for row in batch):
            raise RuntimeError("Life Data returned an invalid row identity")
        rows.update((row["id"], row) for row in batch)
        cursor = page["next_cursor"]
        if cursor is None:
            return [row for row in rows.values() if not row.get("deleted_at")]
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise RuntimeError("Life Data returned an invalid or repeated cursor")
        cursors.add(cursor)
        body["after"] = cursor


def pull_ids(table, *, settings=None, client=None):
    """All non-deleted row IDs, without caching an incomplete scan."""
    return {row["id"] for row in pull_rows(table, [], settings=settings, client=client)}


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def insert_rows(table, rows, *, settings=None, client=None):
    """Insert only; existing IDs (including tombstones) are never overwritten.

    The caller persists and reuses its original IDs on an ambiguous outcome.
    Partial rejection or an incomplete receipt raises; there is no blind retry.
    """
    expected = [row.get("id") for row in rows]
    if not all(_identity(value) for value in expected) or len(set(expected)) != len(expected):
        raise ValueError("Rows require distinct, nonempty string IDs")
    if not rows:
        return {"inserted": [], "existing": [], "rejected": []}
    out = _post(
        "/v1/rows/insert",
        {"table": table, "columns": sorted({k for row in rows for k in row}), "rows": rows},
        settings=settings,
        client=client,
    )
    if not all(isinstance(out.get(key), list) for key in ("inserted", "existing", "rejected")):
        raise RuntimeError("Life Data returned an invalid insert receipt")
    if out["rejected"]:
        raise RuntimeError(f"Life Data rejected {len(out['rejected'])} rows")
    accepted = out["inserted"] + out["existing"]
    if (
        not all(_identity(value) for value in accepted)
        or len(accepted) != len(expected)
        or len(set(accepted)) != len(accepted)
        or set(accepted) != set(expected)
    ):
        raise RuntimeError("Life Data returned an incomplete or ambiguous insert receipt")
    return out


def _stamp(value):
    if not isinstance(value, str) or len(value) != 24 or not value.endswith("Z"):
        return False
    try:
        return (
            datetime.fromisoformat(value).isoformat(timespec="milliseconds")
            == value[:-1] + "+00:00"
        )
    except ValueError:
        return False


def patch_row(table, row_id, values, expected_revision, *, settings=None, client=None):
    """Patch an existing row conditionally, never retrying or falling back to upsert."""
    out = _post(
        "/v1/rows/patch",
        {"table": table, "id": row_id, "values": values, "expected_revision": expected_revision},
        settings=settings,
        client=client,
    )
    revision = out.get("revision")
    if (
        out.get("id") != row_id
        or not isinstance(revision, dict)
        or not _stamp(revision.get("updated_at"))
        or "hub_at" not in revision
        or (revision["hub_at"] is not None and not _stamp(revision["hub_at"]))
    ):
        raise RuntimeError("Life Data returned an invalid patch receipt")
    return out
