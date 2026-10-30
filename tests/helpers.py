"""Shared test helpers — importable by test modules."""

import json
import json as _json
from unittest.mock import MagicMock


def make_gemini_response(json_data):
    """Helper to create a mock Gemini response with .text property."""
    resp = MagicMock()
    resp.text = json.dumps(json_data)
    resp.candidates = [MagicMock(finish_reason="STOP", safety_ratings=[])]
    return resp


class CatalogHub:
    """The hub's `POST /v1/catalog/projection` over a catalog dump (the
    tests/fixtures/catalog.json shape): per requested column its type,
    required, options and static default, plus the table's enforced rule
    texts. A column the dump lacks is refused, as the hub refuses one the
    credential holds no catalog grant for."""

    def __init__(self, raw):
        self.raw, self.calls, self.down = raw, [], False

    def post(self, url, *, json, headers, timeout):
        import requests

        self.calls.append((url, dict(headers), json))
        if self.down:
            raise requests.ConnectionError("hub unreachable")
        table, columns = json["table"], json["columns"]
        props = {
            p["col"]: p
            for p in self.raw["properties"]
            if p["tbl"] == table and not p.get("deleted_at")
        }
        response = requests.Response()
        if not set(columns) <= set(props):
            response.status_code = 403
            response._content = b'{"error": "insufficient scope"}'
            return response

        def project(p):
            out = {"column": p["col"], "type": p["type"], "required": bool(p.get("required"))}
            if p.get("options") is not None:
                options = p["options"]
                options = _json.loads(options) if isinstance(options, str) else options
                out["options"] = [{"v": o} if isinstance(o, str) else o for o in options]
            default = p.get("default_value")
            if isinstance(default, str) and not default.startswith("sql:"):
                out["default"] = default
            return out

        rules = [
            r["text"]
            for r in self.raw.get("rules", [])
            if r.get("tbl") == table
            and r.get("kind") == "invariant"
            and r.get("enforce")
            and r.get("text")
            and not r.get("deleted_at")
        ]
        response.status_code = 200
        response._content = _json.dumps(
            {"table": table, "properties": [project(props[c]) for c in columns], "rules": rules}
        ).encode()
        return response
