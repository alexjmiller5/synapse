"""Shared test helpers — importable by test modules."""

import json
from unittest.mock import MagicMock


def make_gemini_response(json_data):
    """Helper to create a mock Gemini response with .text property."""
    resp = MagicMock()
    resp.text = json.dumps(json_data)
    resp.candidates = [MagicMock(finish_reason="STOP", safety_ratings=[])]
    return resp
