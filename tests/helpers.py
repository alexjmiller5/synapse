"""Shared test helpers — importable by test modules."""

import json
from unittest.mock import MagicMock

from core.config import PROPERTY_IDS


def props_of(call, category):
    """Re-key one create/update call's `properties` from stable ids BACK to names."""
    id_to_name = {pid: name for name, pid in PROPERTY_IDS.get(category, {}).items()}
    return {id_to_name.get(k, k): v for k, v in call.kwargs["properties"].items()}


def sent_props(create_or_update_mock, category):
    """The `properties` the LAST create/update call sent, re-keyed to names so
    assertions stay readable. Synapse now writes properties by id, so this verifies
    the real id-keyed payload while letting tests assert by name."""
    return props_of(create_or_update_mock.call_args, category)


def make_gemini_response(json_data):
    """Helper to create a mock Gemini response with .text property."""
    resp = MagicMock()
    resp.text = json.dumps(json_data)
    resp.candidates = [MagicMock(finish_reason="STOP", safety_ratings=[])]
    return resp


def make_notion_page(page_id, title_key="Name", title_value="Test Page", extra_props=None):
    """Helper to create a Notion page result dict."""
    page = {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "properties": {title_key: {"title": [{"plain_text": title_value}]}},
    }
    if extra_props:
        page["properties"].update(extra_props)
    return page
