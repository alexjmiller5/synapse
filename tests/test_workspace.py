"""Workspaces: the product template (prompts.yaml) plus one user's overlay and credentials."""

import pytest

from core import workspace as ws


def test_overlay_deep_merges_over_the_template_and_lists_replace():
    template = {
        "categories": {
            "tasks": {"description": "d", "properties": {"Tags": {"allowlist": ["A"], "x": 1}}}
        }
    }
    overlay = {
        "categories": {"tasks": {"place_tags": ["P"], "properties": {"Tags": {"allowlist": ["B"]}}}}
    }
    merged = ws.merge(template, overlay)
    assert merged["categories"]["tasks"] == {
        "description": "d",
        "place_tags": ["P"],
        "properties": {"Tags": {"allowlist": ["B"], "x": 1}},
    }
    assert template["categories"]["tasks"]["properties"]["Tags"]["allowlist"] == ["A"]


def test_overlay_cannot_invent_categories_or_properties_the_template_lacks():
    template = {"categories": {"tasks": {"properties": {"Tags": {}}}}}
    with pytest.raises(ws.InvalidOverlay, match="nope"):
        ws.build("w", {"categories": {"nope": {}}}, template=template)
    with pytest.raises(ws.InvalidOverlay, match="Ghost"):
        ws.build("w", {"categories": {"tasks": {"properties": {"Ghost": {}}}}}, template=template)


def test_place_tags_join_the_tags_allowlist_once():
    template = {"categories": {"tasks": {"properties": {"Tags": {"allowlist": ["Chore"]}}}}}
    built = ws.build(
        "w", {"categories": {"tasks": {"place_tags": ["Lake House", "Chore"]}}}, template=template
    )
    tasks = built.config["categories"]["tasks"]
    assert tasks["place_tags"] == ["Lake House", "Chore"]
    assert tasks["properties"]["Tags"]["allowlist"] == ["Chore", "Lake House"]


def test_store_round_trip_keeps_secrets_out_of_the_summary():
    store = {}
    ws.save(store, "alpha", overlay={"workflow": {"projects": {"table": "projects"}}})
    ws.save(store, "alpha", secrets={"soma_hub_url": "https://hub", "soma_hub_token": "secret-t"})
    loaded = ws.load(store, "alpha")
    assert loaded.id == "alpha"
    assert loaded.config["workflow"]["projects"] == {"table": "projects"}
    assert loaded.secrets["soma_hub_token"] == "secret-t"
    assert loaded.store is store
    summary = ws.summary(store, "alpha")
    assert "secret-t" not in repr(summary)
    assert summary["secrets_set"] == ["soma_hub_token", "soma_hub_url"]


def test_unknown_secret_names_are_refused():
    with pytest.raises(ws.InvalidOverlay, match="GEMINI"):
        ws.save({}, "alpha", secrets={"GEMINI_API_KEY": "x"})
    with pytest.raises(ws.InvalidOverlay, match="notion_integration_token"):
        ws.save({}, "alpha", secrets={"notion_integration_token": "x"})


def test_a_record_holding_retired_notion_state_loads_and_sheds_it_on_save():
    store = {
        "workspace:alex": {
            "overlay": {},
            "property_ids": {"tasks": {"Name": "title"}},
            "secrets": {"notion_integration_token": "n", "soma_hub_url": "https://hub"},
        }
    }
    assert ws.load(store, "alex").secrets == {"soma_hub_url": "https://hub"}
    assert ws.summary(store, "alex")["secrets_set"] == ["soma_hub_url"]
    ws.save(store, "alex", secrets={})
    assert set(store["workspace:alex"]) == {"overlay", "secrets", "updated_at"}
    assert store["workspace:alex"]["secrets"] == {"soma_hub_url": "https://hub"}


def test_missing_workspace_is_an_error():
    with pytest.raises(ws.UnknownWorkspace):
        ws.load({}, "ghost")


def test_use_switches_the_active_workspace_and_restores_it():
    before = ws.current()
    other = ws.build("other", {"workflow": {"projects": {"table": "other_projects"}}})
    with ws.use(other):
        assert ws.current().id == "other"
        from core.config import PROMPTS

        assert PROMPTS["workflow"]["projects"]["table"] == "other_projects"
    assert ws.current() is before


def test_the_template_is_prompts_yaml_and_names_no_notion_state():
    assert not (ws.TEMPLATE_DIR / "databases.yaml").exists()
    template = ws.template()
    assert {"categorize_template", "extraction_template", "categories"} <= set(template)
    text = (ws.TEMPLATE_DIR / "prompts.yaml").read_text()
    assert "db_id" not in text and "property_id" not in text


def test_hub_credentials_saved_under_the_pre_soma_keys_still_load():
    store = {
        "workspace:alex": {
            "overlay": {},
            "secrets": {"life_hub_url": "https://hub.example", "life_hub_token": "t"},
        }
    }
    loaded = ws.load(store, "alex")
    assert loaded.secrets == {"soma_hub_url": "https://hub.example", "soma_hub_token": "t"}
    assert ws.summary(store, "alex")["secrets_set"] == ["soma_hub_token", "soma_hub_url"]
    ws.save(store, "alex", secrets={"soma_hub_token": "t2"})
    assert store["workspace:alex"]["secrets"] == {
        "soma_hub_url": "https://hub.example",
        "soma_hub_token": "t2",
    }
