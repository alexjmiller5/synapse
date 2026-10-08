#!/usr/bin/env python
"""Snapshot a workspace's property ids — {category: {prop_name: prop_id}}.

Synapse writes/hydrates Notion properties by their STABLE id (rename-safe), but
keeps human names in the config (the AI needs them). This reads the live
name->id map from the workspace's own Notion and stores it on the workspace.
Re-run whenever a Notion property is ADDED (a rename keeps working via the id).

Run: just sync-prop-ids [workspace]   (SYNAPSE_WORKSPACE names the stored
workspace; without it, writes $SYNAPSE_WORKSPACE_DIR/property_ids.yaml)
"""

import os
import sys

import requests
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import workspace  # noqa: E402
from core.config import DATABASES  # noqa: E402
from core.secrets import get_db_id  # noqa: E402
from store import activate, open_state  # noqa: E402

NOTION = "https://api.notion.com/v1"
HEADERS = {"Notion-Version": "2026-03-11"}
# Non-category DBs Synapse also reads or writes (the top-level `db_ids`).
HELPER_CATEGORIES = ["logs", "projects"]


def data_source_id(db_id):
    r = requests.get(f"{NOTION}/databases/{db_id}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    sources = r.json().get("data_sources") or []
    return sources[0]["id"] if sources else None


def prop_map(ds_id):
    r = requests.get(f"{NOTION}/data_sources/{ds_id}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return {name: p["id"] for name, p in r.json().get("properties", {}).items()}


def main():
    HEADERS["Authorization"] = f"Bearer {workspace.current().secrets['notion_integration_token']}"
    categories = list(DATABASES.get("databases", {}).keys()) + HELPER_CATEGORIES
    out = {}
    for cat in categories:
        db_id = get_db_id(cat)
        if not db_id:
            print(f"  ⚠️ {cat}: no db_id — skipped")
            continue
        try:
            ds = data_source_id(db_id)
            if not ds:
                print(f"  ⚠️ {cat}: no data source — skipped")
                continue
            out[cat] = dict(sorted(prop_map(ds).items()))
            print(f"  ✓ {cat}: {len(out[cat])} properties")
        except Exception as e:
            print(f"  ❌ {cat}: {e}")

    out = dict(sorted(out.items()))
    wid = os.environ.get("SYNAPSE_WORKSPACE")
    if wid:
        workspace.save(open_state(), wid, property_ids=out)
        print(f"\nStored {len(out)} categories on workspace {wid}")
    else:
        path = os.path.join(os.environ["SYNAPSE_WORKSPACE_DIR"], "property_ids.yaml")
        with open(path, "w") as f:
            yaml.safe_dump(out, f, sort_keys=True, allow_unicode=True)
        print(f"\nWrote {len(out)} categories to {path}")


if __name__ == "__main__":
    with activate(os.environ.get("SYNAPSE_WORKSPACE")):
        main()
