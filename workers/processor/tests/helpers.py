"""Shared test helpers — importable by test modules."""

import json
import base64
from unittest.mock import MagicMock


def make_gemini_response(json_data):
    """Helper to create a mock Gemini response with .text property."""
    resp = MagicMock()
    resp.text = json.dumps(json_data)
    resp.candidates = [MagicMock(finish_reason="STOP", safety_ratings=[])]
    return resp


def make_cloud_event(text):
    """Creates a mock CloudEvent with base64-encoded message data."""
    event = MagicMock()
    encoded = base64.b64encode(text.encode("utf-8")).decode("utf-8")
    event.data = {"message": {"data": encoded}}
    return event


def make_notion_page(page_id, title_key="Name", title_value="Test Page", extra_props=None):
    """Helper to create a Notion page result dict."""
    page = {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id.replace('-', '')}",
        "properties": {
            title_key: {
                "title": [{"plain_text": title_value}]
            }
        },
    }
    if extra_props:
        page["properties"].update(extra_props)
    return page
