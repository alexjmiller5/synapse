"""Stateful synthetic HTTP boundary for media capture tests."""

from copy import deepcopy
import requests


class SyntheticHub:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})
        self.writes = []
        self.before_patch = None
        self.before_insert = None
        self.lose_reply = None

    def post(self, url, *, json=None, **kwargs):
        body = json
        route = url.rsplit("/", 1)[-1]
        table = body["table"]
        rows = self.rows.setdefault(table, {})
        status = 200
        if route == "pull":
            chosen = list(rows.values())
            for column, value in body.get("where", {}).items():
                chosen = [r for r in chosen if r.get(column) == value]
            result = {
                "rows": [{c: r.get(c) for c in body["columns"]} for r in chosen],
                "next_cursor": None,
            }
        elif route in ("push", "insert"):
            if self.before_insert and route == "insert":
                self.before_insert(rows)
                self.before_insert = None
            inserted, existing = [], []
            for row in body["rows"]:
                key = row["id"]
                if route == "insert" and key in rows:
                    existing.append(key)
                    continue
                rows.setdefault(key, {}).update(row)
                rows[key]["hub_at"] = "2026-01-01T00:00:00.001Z"
                rows[key].setdefault("deleted_at", None)
                inserted.append(key)
            self.writes.append((route, deepcopy(body)))
            result = (
                {"inserted": inserted, "existing": existing, "rejected": []}
                if route == "insert"
                else {"upserted": len(inserted), "rejected": []}
            )
        elif route == "patch":
            row = rows.get(body["id"])
            if self.before_patch:
                self.before_patch(row)
                self.before_patch = None
            revision = {c: row.get(c) for c in ("updated_at", "hub_at")} if row else None
            if not row or row.get("deleted_at") or revision != body["expected_revision"]:
                status, result = 409, {"error": "revision_conflict"}
            else:
                row.update(body["values"])
                row["updated_at"] = row["hub_at"] = "2026-01-02T00:00:00.000Z"
                self.writes.append((route, deepcopy(body)))
                result = {
                    "id": row["id"],
                    "revision": {c: row[c] for c in ("updated_at", "hub_at")},
                }
        else:
            raise AssertionError(f"Unsupported transport: {route}")
        if self.lose_reply == route:
            self.lose_reply = None
            raise requests.Timeout("lost reply")
        response = requests.Response()
        response.status_code = status
        response._content = __import__("json").dumps(result).encode()
        return response
