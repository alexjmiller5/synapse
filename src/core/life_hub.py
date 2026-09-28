"""life-data hub client — the one place Synapse writes rows to life-data.

Movies and TV shows are life-data tables (not Notion DBs); their derived
metadata (title, genres, cast, poster) is filled in on the hub, so Synapse
sends only the columns it actually knows.
"""

import requests

from core.settings import get_settings


def push_rows(table, rows, *, settings=None, client=None):
    """Upsert `rows` into a life-data `table`. Returns {"upserted": n, "rejected": [...]}.

    Only the columns present in the rows are sent, and the hub's upsert touches
    exactly those — a status-only update never clobbers tags or date_watched.
    A rejected row comes back as {id, col, rule, message}; the caller decides
    what to do with it. Raises on a non-2xx response.
    """
    settings = settings or get_settings()
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
    settings = settings or get_settings()
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
