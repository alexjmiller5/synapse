"""Workspaces: the product template plus one user's overlay, credentials and ids."""

import pytest

from core import workspace as ws


def test_overlay_deep_merges_over_the_template_and_lists_replace():
    template = {
        "databases": {
            "tasks": {"description": "d", "properties": {"Tags": {"allowlist": ["A"], "type": "x"}}}
        }
    }
    overlay = {
        "databases": {"tasks": {"db_id": "t1", "properties": {"Tags": {"allowlist": ["B", "C"]}}}}
    }
    merged = ws.merge(template, overlay)
    assert merged["databases"]["tasks"] == {
        "description": "d",
        "db_id": "t1",
        "properties": {"Tags": {"allowlist": ["B", "C"], "type": "x"}},
    }
    assert template["databases"]["tasks"]["properties"]["Tags"]["allowlist"] == ["A"]  # untouched


def test_overlay_cannot_invent_categories_or_properties_the_template_lacks():
    template = {"databases": {"tasks": {"properties": {"Tags": {}}}}}
    with pytest.raises(ws.InvalidOverlay, match="nope"):
        ws.build("w", {"databases": {"nope": {}}}, template=template)
    with pytest.raises(ws.InvalidOverlay, match="Ghost"):
        ws.build("w", {"databases": {"tasks": {"properties": {"Ghost": {}}}}}, template=template)


def test_place_tags_join_the_tags_allowlist_once():
    template = {"databases": {"tasks": {"properties": {"Tags": {"allowlist": ["Chore"]}}}}}
    built = ws.build(
        "w", {"databases": {"tasks": {"place_tags": ["Lake House", "Chore"]}}}, template=template
    )
    tasks = built.databases["databases"]["tasks"]
    assert tasks["place_tags"] == ["Lake House", "Chore"]
    assert tasks["properties"]["Tags"]["allowlist"] == ["Chore", "Lake House"]


def test_store_round_trip_keeps_secrets_out_of_the_summary():
    store = {}
    ws.save(
        store,
        "alpha",
        overlay={"db_ids": {"projects": "p1"}},
        property_ids={"tasks": {"Name": "title"}},
    )
    ws.save(
        store,
        "alpha",
        secrets={"notion_integration_token": "secret-n", "life_hub_url": "https://hub"},
    )
    loaded = ws.load(store, "alpha")
    assert loaded.id == "alpha"
    assert loaded.databases["db_ids"]["projects"] == "p1"
    assert loaded.property_ids == {"tasks": {"Name": "title"}}
    assert loaded.secrets["notion_integration_token"] == "secret-n"
    summary = ws.summary(store, "alpha")
    assert "secret-n" not in repr(summary)
    assert summary["secrets_set"] == ["life_hub_url", "notion_integration_token"]


def test_unknown_secret_names_are_refused():
    with pytest.raises(ws.InvalidOverlay, match="GEMINI"):
        ws.save({}, "alpha", secrets={"GEMINI_API_KEY": "x"})


def test_missing_workspace_is_an_error():
    with pytest.raises(ws.UnknownWorkspace):
        ws.load({}, "ghost")


def test_use_switches_the_active_workspace_and_restores_it():
    before = ws.current()
    other = ws.build("other", {"db_ids": {"projects": "other-projects"}})
    with ws.use(other):
        assert ws.current().id == "other"
        from core.config import DATABASES

        assert DATABASES["db_ids"]["projects"] == "other-projects"
    assert ws.current() is before


def test_template_ships_no_notion_ids():
    template = ws.template()
    assert "db_ids" not in template or not any(template["db_ids"].values())
    assert not [c for c, d in template["databases"].items() if d.get("db_id")]
