"""The Soma catalog slice: fetched once, cached in the operational store, refreshed
on a TTL and after the hub rejects a write."""

import copy
import json
from pathlib import Path

import pytest
import requests

from core import catalog, soma_hub, workspace

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())


class Hub:
    """GET /v1/catalog with ETag revalidation, counting requests."""

    def __init__(self, raw=FIXTURE, etag='"v1"'):
        self.raw, self.etag, self.calls, self.down = raw, etag, [], False

    def get(self, url, *, headers, timeout):
        self.calls.append((url, dict(headers)))
        if self.down:
            raise requests.ConnectionError("hub unreachable")
        response = requests.Response()
        if headers.get("If-None-Match") == self.etag:
            response.status_code = 304
            response._content = b""
        else:
            response.status_code = 200
            response._content = json.dumps(self.raw).encode()
        response.headers["ETag"] = self.etag
        return response


@pytest.fixture
def hub(monkeypatch):
    fake = Hub()
    monkeypatch.setattr(catalog, "requests", fake)
    return fake


@pytest.fixture
def ws():
    """A stored workspace (operational store present) with a clock the test controls."""
    base = copy.deepcopy(workspace.current())
    base.store, base.catalog_memo = {}, None
    with workspace.use(base):
        yield base


def test_slice_keeps_only_the_tables_synapse_writes_with_parsed_options(hub, ws):
    groceries = catalog.table("groceries")
    status = groceries["columns"]["status"]
    assert status["required"] is True and status["default"] == "On List"
    assert status["options"][0] == {
        "v": "On List",
        "d": "Need to buy it; the default for a new capture.",
    }
    assert "One live grocery item per name (case-insensitive)." in groceries["rules"]
    assert "people" not in ws.store["catalog:default"]["catalog"]
    assert hub.calls[0][0] == "https://hub.test.invalid/v1/catalog"
    assert hub.calls[0][1]["Authorization"] == "Bearer fake-hub-token"


def test_only_enforced_invariants_become_rules(hub, ws):
    rules = catalog.table("movies")["rules"]
    assert any("soft-deleted" in rule for rule in rules)
    assert not any("date_watched has a terminal status" in rule for rule in rules)
    assert not any("There is no Watched status" in rule for rule in rules)


def test_a_fresh_cache_is_read_without_asking_the_hub(hub, ws, monkeypatch):
    catalog.table("groceries")
    ws.catalog_memo = None  # a new container: only the store survives
    catalog.table("groceries")
    assert len(hub.calls) == 1


def test_a_stale_cache_is_revalidated_by_etag(hub, ws, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(catalog.time, "time", lambda: clock[0])
    catalog.table("groceries")
    clock[0] += catalog.TTL_S + 1
    ws.catalog_memo = None
    assert catalog.table("groceries")["columns"]["status"]["default"] == "On List"
    assert hub.calls[1][1]["If-None-Match"] == '"v1"'
    assert ws.store["catalog:default"]["fetched_at"] == clock[0]


def test_a_changed_catalog_replaces_the_cache(hub, ws, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(catalog.time, "time", lambda: clock[0])
    catalog.table("groceries")
    hub.raw = copy.deepcopy(FIXTURE)
    for prop in hub.raw["properties"]:
        if (prop["tbl"], prop["col"]) == ("groceries", "status"):
            prop["default_value"] = "Have"
    hub.etag = '"v2"'
    clock[0] += catalog.TTL_S + 1
    ws.catalog_memo = None
    assert catalog.table("groceries")["columns"]["status"]["default"] == "Have"
    assert ws.store["catalog:default"]["etag"] == '"v2"'


def test_an_unreachable_hub_falls_back_to_the_cached_copy(hub, ws, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(catalog.time, "time", lambda: clock[0])
    catalog.table("groceries")
    hub.down = True
    clock[0] += catalog.TTL_S + 1
    ws.catalog_memo = None
    assert catalog.table("groceries")["columns"]["name"]["required"] is True


def test_no_cache_and_no_hub_is_an_error(hub, ws):
    hub.down = True
    with pytest.raises(requests.ConnectionError):
        catalog.table("groceries")


def test_a_hub_rejection_forces_the_next_load_to_refetch(hub, ws, monkeypatch):
    catalog.table("groceries")
    monkeypatch.setattr(soma_hub.requests, "post", _rejecting_push)
    soma_hub.push_rows("groceries", [{"id": "x", "name": "Milk"}])
    assert "catalog:default" not in ws.store
    catalog.table("groceries")
    assert len(hub.calls) == 2


def test_an_insert_rejection_forces_a_refetch(hub, ws, monkeypatch):
    catalog.table("groceries")
    monkeypatch.setattr(soma_hub.requests, "post", _rejecting_insert)
    with pytest.raises(soma_hub.InsertRejected):
        soma_hub.insert_rows("groceries", [{"id": "x", "name": "Milk"}])
    assert "catalog:default" not in ws.store


@pytest.mark.parametrize("status", [400, 422])
def test_a_validation_status_forces_a_refetch(hub, ws, monkeypatch, status):
    catalog.table("groceries")
    monkeypatch.setattr(soma_hub.requests, "post", lambda *a, **k: _response(status, {}))
    with pytest.raises(requests.HTTPError):
        soma_hub.patch_row("groceries", "x", {"status": "Have"}, {})
    assert "catalog:default" not in ws.store


def test_an_auth_failure_keeps_the_cache(hub, ws, monkeypatch):
    catalog.table("groceries")
    monkeypatch.setattr(soma_hub.requests, "post", lambda *a, **k: _response(403, {}))
    with pytest.raises(requests.HTTPError):
        soma_hub.patch_row("groceries", "x", {"status": "Have"}, {})
    assert "catalog:default" in ws.store


def _response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    return response


def _rejecting_push(url, **kwargs):
    return _response(200, {"upserted": 0, "rejected": [{"id": "x", "message": "bad"}]})


def _rejecting_insert(url, **kwargs):
    rejected = [{"id": "x", "col": "category", "rule": "required", "message": "missing"}]
    return _response(200, {"inserted": [], "existing": [], "rejected": rejected})
