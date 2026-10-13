"""The Soma catalog slice: one projection per table the prompts use, cached in the
operational store, refreshed on a TTL and after the hub rejects a write."""

import copy
import json
from pathlib import Path

import pytest
import requests

from core import catalog, soma_hub, workspace
from helpers import CatalogHub

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text())
PROJECTION = "https://hub.test.invalid/v1/catalog/projection"


@pytest.fixture
def hub(monkeypatch):
    fake = CatalogHub(FIXTURE)
    monkeypatch.setattr(catalog, "requests", fake)
    return fake


@pytest.fixture
def ws():
    """A stored workspace (operational store present) with a clock the test controls."""
    base = copy.deepcopy(workspace.current())
    base.store, base.catalog_memo = {}, None
    with workspace.use(base):
        yield base


def test_each_table_is_projected_for_exactly_the_columns_its_prompts_use(hub, ws):
    catalog.table("groceries")
    asked = {body["table"]: body["columns"] for _, _, body in hub.calls}
    assert asked["groceries"] == ["category", "name", "status"]
    # The tasks binding maps many columns; the prompt reads only its properties'.
    assert asked["tasks"] == ["due_date", "lane", "links", "priority", "tags", "title"]
    # Two categories share things_to_do; workflow-only tables need no contract.
    assert asked["things_to_do"] == ["city", "status", "tags", "title"]
    assert "synapse_executions" not in asked and "projects" not in asked
    assert {url for url, _, _ in hub.calls} == {PROJECTION}
    assert hub.calls[0][1]["Authorization"] == "Bearer fake-hub-token"


def test_a_table_without_prompt_columns_is_not_projected(hub, ws):
    ws.config["categories"]["ideas"]["columns"] = {}
    catalog.table("groceries")
    assert "ideas" not in {body["table"] for _, _, body in hub.calls}


def test_slice_keeps_types_options_static_defaults_and_rule_texts(hub, ws):
    groceries = catalog.table("groceries")
    status = groceries["columns"]["status"]
    assert status["required"] is True and status["default"] == "On List"
    assert status["options"][0] == {
        "v": "On List",
        "d": "Need to buy it; the default for a new capture.",
    }
    assert "One live grocery item per name (case-insensitive)." in groceries["rules"]
    assert "people" not in ws.store["catalog:default"]["catalog"]


def test_only_enforced_invariants_become_rules(hub, ws):
    rules = catalog.table("movies")["rules"]
    assert any("soft-deleted" in rule for rule in rules)
    assert not any("date_watched has a terminal status" in rule for rule in rules)
    assert not any("There is no Watched status" in rule for rule in rules)


def test_a_fresh_cache_is_read_without_asking_the_hub(hub, ws, monkeypatch):
    catalog.table("groceries")
    asked = len(hub.calls)
    ws.catalog_memo = None  # a new container: only the store survives
    catalog.table("groceries")
    assert len(hub.calls) == asked


def test_a_stale_cache_is_projected_again(hub, ws, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(catalog.time, "time", lambda: clock[0])
    catalog.table("groceries")
    asked = len(hub.calls)
    hub.raw = copy.deepcopy(FIXTURE)
    for prop in hub.raw["properties"]:
        if (prop["tbl"], prop["col"]) == ("groceries", "status"):
            prop["default_value"] = "Have"
    clock[0] += catalog.TTL_S + 1
    ws.catalog_memo = None
    assert catalog.table("groceries")["columns"]["status"]["default"] == "Have"
    assert len(hub.calls) == 2 * asked
    assert ws.store["catalog:default"]["fetched_at"] == clock[0]


def test_a_changed_config_reprojects_before_the_ttl(hub, ws):
    catalog.table("groceries")
    asked = len(hub.calls)
    ws.config["categories"]["groceries"]["columns"].pop("Category")
    ws.catalog_memo = None
    assert "category" not in catalog.table("groceries")["columns"]
    assert len(hub.calls) == 2 * asked


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


def test_a_refused_projection_leaves_only_that_table_without_a_contract(hub, ws):
    # The hub refuses a column it holds no catalog row (or grant) for: the
    # table's prompt states no contract, and the hub still validates its writes.
    hub.raw = {**FIXTURE, "properties": [p for p in FIXTURE["properties"] if p["col"] != "lane"]}
    assert catalog.table("tasks") == {"columns": {}, "rules": []}
    assert catalog.table("groceries")["columns"]["name"]["required"] is True


def test_an_insert_rejection_forces_the_next_load_to_refetch(hub, ws, monkeypatch):
    catalog.table("groceries")
    asked = len(hub.calls)
    monkeypatch.setattr(soma_hub.requests, "post", _rejecting_insert)
    with pytest.raises(soma_hub.InsertRejected):
        soma_hub.insert_rows("groceries", [{"id": "x", "name": "Milk"}])
    assert "catalog:default" not in ws.store
    catalog.table("groceries")
    assert len(hub.calls) == 2 * asked


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


def _rejecting_insert(url, **kwargs):
    rejected = [{"id": "x", "col": "category", "rule": "required", "message": "missing"}]
    return _response(200, {"inserted": [], "existing": [], "rejected": rejected})
