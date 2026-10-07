"""life-data hub client — the one place Synapse writes rows to life-data.

Movies and TV shows are life-data tables (not Notion DBs); their derived
metadata (title, genres, cast, poster) is filled in on the hub, so Synapse
sends only the columns it actually knows.
"""

import requests

from types import SimpleNamespace

from core.workspace import current


def _hub():
    """The active workspace's life-data hub (url + token)."""
    s = current().secrets
    return SimpleNamespace(
        life_hub_url=s.get("life_hub_url"), life_hub_token=s.get("life_hub_token")
    )


def push_rows(table, rows, *, settings=None, client=None):
    """Upsert `rows` into a life-data `table`. Returns {"upserted": n, "rejected": [...]}.

    Only the columns present in the rows are sent, and the hub's upsert touches
    exactly those — a status-only update never clobbers tags or date_watched.
    A rejected row comes back as {id, col, rule, message}; the caller decides
    what to do with it. Raises on a non-2xx response.
    """
    settings = settings or _hub()
    if not settings.life_hub_url or not settings.life_hub_token:
        raise RuntimeError("LIFE_HUB_URL / LIFE_HUB_TOKEN are not configured")

    resp = (client or requests).post(
        f"{settings.life_hub_url.rstrip('/')}/v1/rows/push",
        json={
            "table": table,
            "columns": sorted({k for row in rows for k in row}),
            "rows": rows,
        },
        headers={
            "Authorization": f"Bearer {settings.life_hub_token}",
            # Cloudflare's bot protection 403s a default Python user agent
            # (error 1010) before the request ever reaches the Worker.
            "User-Agent": "synapse",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def pull_rows(table, columns, *, settings=None, client=None):
    """The non-deleted rows of a life-data `table`, restricted to `columns`.

    Read against the hub's actual state (never an in-run cache) so a capture
    can find the row it should update - a grocery by name, a bookmark by url.
    """
    settings = settings or _hub()
    if not settings.life_hub_url or not settings.life_hub_token:
        raise RuntimeError("LIFE_HUB_URL / LIFE_HUB_TOKEN are not configured")

    cols = ["id", "deleted_at"] + [c for c in columns if c not in ("id", "deleted_at")]
    resp = (client or requests).post(
        f"{settings.life_hub_url.rstrip('/')}/v1/rows/pull",
        json={"table": table, "columns": cols, "since": ""},
        headers={
            "Authorization": f"Bearer {settings.life_hub_token}",
            "User-Agent": "synapse",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return [r for r in resp.json().get("rows", []) if not r.get("deleted_at")]


def pull_ids(table, *, settings=None, client=None):
    """The set of non-deleted row ids currently in a life-data `table`."""
    return {r["id"] for r in pull_rows(table, [], settings=settings, client=client)}


def _post_rows(route, body, *, settings=None, client=None):
    settings = settings or _hub()
    if not settings.life_hub_url or not settings.life_hub_token:
        raise RuntimeError("LIFE_HUB_URL / LIFE_HUB_TOKEN are not configured")
    response = (client or requests).post(
        f"{settings.life_hub_url.rstrip('/')}/v1/rows/{route}",
        json=body,
        headers={
            "Authorization": f"Bearer {settings.life_hub_token}",
            "User-Agent": "synapse",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def read_row(table, identity, columns, *, settings=None, client=None):
    """A bounded identity read, including tombstones for explicit save decisions."""
    result = _post_rows(
        "pull",
        {
            "table": table,
            "columns": sorted(set(columns) | {"id", "updated_at", "hub_at", "deleted_at"}),
            "where": {"id": identity},
            "limit": 2,
        },
        settings=settings,
        client=client,
    )
    rows = result.get("rows")
    if not isinstance(rows, list) or len(rows) > 1 or result.get("next_cursor"):
        raise ValueError("invalid identity read receipt")
    if not rows:
        return None
    if rows[0].get("id") != identity or not isinstance(rows[0].get("updated_at"), str):
        raise ValueError("invalid identity read receipt")
    return rows[0]


def insert_rows(table, rows, *, settings=None, client=None):
    result = _post_rows(
        "insert",
        {"table": table, "columns": sorted({key for row in rows for key in row}), "rows": rows},
        settings=settings,
        client=client,
    )
    if not isinstance(result, dict) or any(
        not isinstance(result.get(key), list) for key in ("inserted", "existing", "rejected")
    ):
        raise ValueError("invalid insert receipt")
    acknowledged = (
        result["inserted"] + result["existing"] + [r.get("id") for r in result["rejected"]]
    )
    if len(set(acknowledged)) != len(acknowledged) or set(acknowledged) != {
        row["id"] for row in rows
    }:
        raise ValueError("invalid insert receipt")
    return result


def patch_row(table, identity, values, expected_revision, *, settings=None, client=None):
    result = _post_rows(
        "patch",
        {"table": table, "id": identity, "values": values, "expected_revision": expected_revision},
        settings=settings,
        client=client,
    )
    if (
        not isinstance(result, dict)
        or result.get("id") != identity
        or not isinstance(result.get("revision"), dict)
        or not isinstance(result["revision"].get("updated_at"), str)
        or "hub_at" not in result["revision"]
    ):
        raise ValueError("invalid patch receipt")
    return result
