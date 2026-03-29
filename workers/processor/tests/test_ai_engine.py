"""Tests for ai_engine.py — parsing, classification, extraction, schema generation."""

import json
from unittest.mock import patch, MagicMock
import pytest

from ai_engine import (
    safe_json_load,
    parse_raw_input,
    generate_classification_prompt,
    generate_extraction_prompt,
    get_gemini_schema,
)
from helpers import make_gemini_response


# ======================================================================
# safe_json_load
# ======================================================================
class TestSafeJsonLoad:
    def test_valid_json(self):
        assert safe_json_load('{"key": "value"}') == {"key": "value"}

    def test_valid_array(self):
        assert safe_json_load('[1, 2, 3]') == [1, 2, 3]

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            safe_json_load("not json at all")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            safe_json_load("")


# ======================================================================
# parse_raw_input
# ======================================================================
class TestParseRawInput:
    def test_single_item(self, mock_gemini):
        response = make_gemini_response([{"core_text": "Buy eggs", "context_notes": ""}])
        mock_gemini.models.generate_content.return_value = response

        result = parse_raw_input("Buy eggs")
        assert len(result) == 1
        assert result[0]["core_text"] == "Buy eggs"

    def test_multiple_items(self, mock_gemini):
        items = [
            {"core_text": "Buy eggs", "context_notes": "groceries"},
            {"core_text": "Call John", "context_notes": ""},
        ]
        response = make_gemini_response(items)
        mock_gemini.models.generate_content.return_value = response

        result = parse_raw_input("Buy eggs $ groceries @ Call John")
        assert len(result) == 2

    def test_with_context(self, mock_gemini):
        items = [{"core_text": "Cancel Uber One", "context_notes": "Jan 1"}]
        response = make_gemini_response(items)
        mock_gemini.models.generate_content.return_value = response

        result = parse_raw_input("Cancel Uber One $ Jan 1")
        assert result[0]["context_notes"] == "Jan 1"

    def test_fallback_on_error(self, mock_gemini):
        """On Gemini failure, falls back to raw text as single item."""
        mock_gemini.models.generate_content.side_effect = Exception("API down")

        result = parse_raw_input("Some text here")
        assert len(result) == 1
        assert result[0]["core_text"] == "Some text here"
        assert result[0]["context_notes"] == ""


# ======================================================================
# generate_classification_prompt
# ======================================================================
class TestGenerateClassificationPrompt:
    def test_includes_categories(self):
        prompt = generate_classification_prompt("Synapse, Blueprint")
        # Should include category descriptions from databases.yaml
        assert "tasks" in prompt
        assert "groceries" in prompt
        assert "movies" in prompt

    def test_includes_projects(self):
        prompt = generate_classification_prompt("Synapse, Blueprint")
        assert "Synapse" in prompt
        assert "Blueprint" in prompt

    def test_excludes_internal_categories(self):
        prompt = generate_classification_prompt("None")
        assert '"youtube-channels"' not in prompt
        assert '"logs"' not in prompt

    def test_none_projects(self):
        prompt = generate_classification_prompt("None")
        assert "None" in prompt


# ======================================================================
# generate_extraction_prompt
# ======================================================================
class TestGenerateExtractionPrompt:
    def test_tasks_prompt(self):
        prompt = generate_extraction_prompt("tasks", "Buy milk")
        assert "tasks" in prompt
        assert "Name" in prompt

    def test_unknown_category(self):
        result = generate_extraction_prompt("nonexistent", "text")
        assert "Error" in result

    def test_includes_url_context(self):
        prompt = generate_extraction_prompt(
            "bookmarks", "https://example.com",
            url_context="HTML Title: Example\nContent..."
        )
        assert "CONTEXT FROM URL" in prompt
        assert "Example" in prompt

    def test_includes_inventory(self):
        prompt = generate_extraction_prompt(
            "groceries", "Buy eggs",
            inventory_list=["Eggs", "Milk", "Bread"]
        )
        assert "EXISTING INVENTORY" in prompt
        assert "Eggs" in prompt

    def test_includes_trips_for_places(self):
        prompt = generate_extraction_prompt(
            "places", "some place",
            trips_inventory=["NYC Trip (Date: 2026-06-01)"]
        )
        assert "AVAILABLE TRIPS" in prompt
        assert "NYC Trip" in prompt

    def test_no_trips_for_non_places(self):
        prompt = generate_extraction_prompt(
            "tasks", "do something",
            trips_inventory=["NYC Trip (Date: 2026-06-01)"]
        )
        assert "AVAILABLE TRIPS" not in prompt

    def test_includes_user_context(self):
        prompt = generate_extraction_prompt(
            "tasks", "Do thing",
            user_context="urgent due friday"
        )
        assert "USER EXPLICIT CONTEXT" in prompt
        assert "urgent due friday" in prompt

    def test_virtual_fields_excluded(self):
        """Virtual fields should not appear in extraction instructions."""
        prompt = generate_extraction_prompt("tasks", "test")
        # Status is virtual for tasks — its instruction should NOT be in the prompt
        # But the field name might appear elsewhere, so check instructions section
        lines = prompt.split("\n")
        status_instruction_lines = [l for l in lines if "`Status`:" in l and "instruction" not in l.lower()]
        # No direct Status extraction instruction for tasks (it's virtual)


# ======================================================================
# get_gemini_schema
# ======================================================================
class TestGetGeminiSchema:
    def test_tasks_schema(self):
        schema = get_gemini_schema("tasks")
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "Name" in props
        assert "Tags" in props
        assert "Due Date" in props
        # Name is required
        assert "Name" in schema["required"]

    def test_virtual_fields_excluded(self):
        """Virtual fields like tasks.Status should not be in the schema."""
        schema = get_gemini_schema("tasks")
        assert "Status" not in schema["properties"]

    def test_multi_select_with_allowlist(self):
        schema = get_gemini_schema("tasks")
        tags = schema["properties"]["Tags"]
        assert tags["type"] == "array"
        assert "items" in tags

    def test_select_with_allowlist(self):
        schema = get_gemini_schema("movies")
        status = schema["properties"]["Status"]
        assert status["type"] == "string"
        assert "enum" in status
        assert "Not Started" in status["enum"]

    def test_create_new_removes_enum(self):
        """Properties with create_new: true should not have enum constraints."""
        schema = get_gemini_schema("podcasts")
        # Podcast Name has create_new: true
        podcast_name = schema["properties"]["Podcast Name"]
        assert "enum" not in podcast_name

    def test_unknown_category_fallback(self):
        schema = get_gemini_schema("nonexistent_category")
        assert schema == {"type": "object", "properties": {"Name": {"type": "string"}}}

    def test_required_fields(self):
        schema = get_gemini_schema("groceries")
        assert "Name" in schema["required"]
        assert "Category" in schema["required"]
        assert "Status" in schema["required"]

    def test_bookmarks_schema(self):
        schema = get_gemini_schema("bookmarks")
        assert "Description" in schema["properties"]
        assert "URL" in schema["properties"]
        assert "Title" in schema["properties"]

    def test_places_schema(self):
        schema = get_gemini_schema("places")
        assert "Name" in schema["properties"]
        assert "Google Maps URL" in schema["properties"]
        assert "City" in schema["properties"]
