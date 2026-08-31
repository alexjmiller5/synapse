"""Tests for ai_engine.py — parsing, classification, extraction, schema generation."""

import pytest
from google.genai import types
from google.genai.errors import ClientError

from core import ai_engine
from core.ai_engine import (
    safe_json_load,
    parse_raw_input,
    generate_classification_prompt,
    generate_extraction_prompt,
    generate_with_retry,
    get_gemini_schema,
)
from helpers import make_gemini_response


# ======================================================================
# GEMINI_MODEL constant + 404 fallback
# ======================================================================
class TestModelFallback:
    def _config(self):
        return types.GenerateContentConfig(response_mime_type="application/json")

    def _not_found(self):
        return ClientError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}})

    def test_model_constant_exists(self):
        assert ai_engine.GEMINI_MODEL
        assert ai_engine.GEMINI_FALLBACK_MODEL

    def test_404_falls_back_to_fallback_model(self, mock_gemini):
        good = make_gemini_response({"ok": True})
        mock_gemini.models.generate_content.side_effect = [self._not_found(), good]

        resp = generate_with_retry(model=ai_engine.GEMINI_MODEL, contents=[], config=self._config())

        assert resp is good
        assert mock_gemini.models.generate_content.call_count == 2
        second_call = mock_gemini.models.generate_content.call_args_list[1]
        assert second_call.kwargs["model"] == ai_engine.GEMINI_FALLBACK_MODEL

    def test_404_on_fallback_model_raises(self, mock_gemini):
        mock_gemini.models.generate_content.side_effect = [self._not_found(), self._not_found()]

        with pytest.raises(ClientError):
            generate_with_retry(model=ai_engine.GEMINI_MODEL, contents=[], config=self._config())

    def test_non_404_client_error_raises(self, mock_gemini):
        err = ClientError(400, {"error": {"message": "bad request", "status": "INVALID_ARGUMENT"}})
        mock_gemini.models.generate_content.side_effect = err

        with pytest.raises(ClientError):
            generate_with_retry(model=ai_engine.GEMINI_MODEL, contents=[], config=self._config())
        assert mock_gemini.models.generate_content.call_count == 1


# ======================================================================
# safe_json_load
# ======================================================================
class TestSafeJsonLoad:
    def test_valid_json(self):
        assert safe_json_load('{"key": "value"}') == {"key": "value"}

    def test_valid_array(self):
        assert safe_json_load("[1, 2, 3]") == [1, 2, 3]

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Malformed JSON"):
            safe_json_load("not json at all")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            safe_json_load("")

    def test_none_raises_valueerror_not_typeerror(self):
        """An empty Gemini response (None) must raise a retryable ValueError,
        not the un-retryable TypeError from json.loads(None)."""
        with pytest.raises(ValueError):
            safe_json_load(None)

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            safe_json_load("   \n  ")


# ======================================================================
# parse_raw_input
# ======================================================================
class TestParseRawInput:
    def test_single_item(self, mock_gemini):
        result = parse_raw_input("Buy eggs")
        assert len(result) == 1
        assert result[0]["core_text"] == "Buy eggs"

    def test_no_delimiters_skips_gemini(self, mock_gemini):
        """No '@'/'$' → nothing to split, so the LLM must not see the text at
        all: round-tripping a bare URL through Gemini mangled repo names
        (github.com/kunchenguid/axi came back as axi_)."""
        result = parse_raw_input("https://github.com/kunchenguid/axi\n")
        assert result == [{"core_text": "https://github.com/kunchenguid/axi", "context_notes": ""}]
        mock_gemini.models.generate_content.assert_not_called()

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

        result = parse_raw_input("Some text $ here")
        assert len(result) == 1
        assert result[0]["core_text"] == "Some text $ here"
        assert result[0]["context_notes"] == ""
        mock_gemini.models.generate_content.assert_called_once()


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

    def test_excludes_helper_dbs(self):
        """Helper DBs (trips/logs/youtube-channels) must not be classification targets.

        They exist only to be *related to* by other categories. A 'plan a trip'
        thought is a task, not a trips-DB write. Regression for pages 35e0…/3650…/36d0….
        """
        prompt = generate_classification_prompt("None")
        assert '"trips"' not in prompt
        assert '"logs"' not in prompt
        assert '"youtube-channels"' not in prompt

    def test_keeps_trip_related_classifiable_categories(self):
        """'places' relates to trips but is itself a real classification target."""
        prompt = generate_classification_prompt("None")
        assert '"places"' in prompt
        assert '"tasks"' in prompt

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

    def test_ai_ready_instruction_present(self):
        """The 'AI Ready' checkbox instruction must carry the explicit-intent
        guidance AND the project-name caveat so the model doesn't false-tick."""
        prompt = generate_extraction_prompt("tasks", "have ai do this")
        assert "AI Ready" in prompt
        assert "for ai" in prompt.lower()
        assert "DEFAULT is false" in prompt
        assert "PROJECT NAME" in prompt

    def test_includes_url_context(self):
        prompt = generate_extraction_prompt(
            "bookmarks", "https://example.com", url_context="HTML Title: Example\nContent..."
        )
        assert "CONTEXT FROM URL" in prompt
        assert "Example" in prompt

    def test_includes_inventory(self):
        prompt = generate_extraction_prompt(
            "groceries", "Buy eggs", inventory_list=["Eggs", "Milk", "Bread"]
        )
        assert "EXISTING INVENTORY" in prompt
        assert "Eggs" in prompt

    def test_includes_trips_for_places(self):
        prompt = generate_extraction_prompt(
            "places", "some place", trips_inventory=["NYC Trip (Date: 2026-06-01)"]
        )
        assert "AVAILABLE TRIPS" in prompt
        assert "NYC Trip" in prompt

    def test_no_trips_for_non_places(self):
        prompt = generate_extraction_prompt(
            "tasks", "do something", trips_inventory=["NYC Trip (Date: 2026-06-01)"]
        )
        assert "AVAILABLE TRIPS" not in prompt

    def test_trips_dates_instruction_present(self):
        """trips.Dates must carry extraction guidance so the AI emits ISO 8601 (or omits)."""
        prompt = generate_extraction_prompt("trips", "Miami trip in September")
        assert "Dates" in prompt
        assert "YYYY-MM-DD" in prompt

    def test_includes_user_context(self):
        prompt = generate_extraction_prompt("tasks", "Do thing", user_context="urgent due friday")
        assert "USER EXPLICIT CONTEXT" in prompt
        assert "urgent due friday" in prompt

    def test_virtual_fields_excluded(self):
        """Virtual fields should not appear in extraction instructions."""
        prompt = generate_extraction_prompt("tasks", "test")
        # Status is virtual for tasks — no instruction line ("- `Status`: ...") in the prompt
        assert not any(line.strip().startswith("- `Status`:") for line in prompt.split("\n"))


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

    def test_checkbox_maps_to_boolean(self):
        """A checkbox prop (tasks.'AI Ready') becomes a JSON-schema boolean field."""
        schema = get_gemini_schema("tasks")
        assert schema["properties"]["AI Ready"] == {"type": "boolean"}


# ======================================================================
# Enum size cap (Gemini 400 INVALID_ARGUMENT on huge enums)
# ======================================================================
class TestEnumCap:
    """Gemini rejects response schemas whose enums exceed its constrained-decoding
    grammar limit (~150 distinct real-world names). Open-world fields must never
    be enum-constrained, and any hydrated option list past MAX_ENUM_OPTIONS must
    drop its enum instead of 400ing every capture in that category."""

    def _movies_props(self):
        from core.config import DATABASES

        return DATABASES["databases"]["movies"]["properties"]

    def test_movie_open_world_fields_have_no_enum(self, monkeypatch):
        """Director / Famous Cast Members are create_new — no enum even when hydrated."""
        props = self._movies_props()
        monkeypatch.setitem(props["Director"], "_runtime_options", ["A Director"])
        monkeypatch.setitem(props["Famous Cast Members"], "_runtime_options", ["An Actor"])
        schema = get_gemini_schema("movies")
        assert "enum" not in schema["properties"]["Director"]
        assert "enum" not in schema["properties"]["Famous Cast Members"]["items"]

    def test_tv_open_world_fields_have_no_enum(self, monkeypatch):
        from core.config import DATABASES

        props = DATABASES["databases"]["tv-shows"]["properties"]
        monkeypatch.setitem(props["Director"], "_runtime_options", ["A Director"])
        monkeypatch.setitem(props["Famous Cast Members"], "_runtime_options", ["An Actor"])
        schema = get_gemini_schema("tv-shows")
        assert "enum" not in schema["properties"]["Director"]
        assert "enum" not in schema["properties"]["Famous Cast Members"]["items"]

    def test_enum_dropped_above_cap(self, monkeypatch):
        """A strict field whose live options outgrow the cap loses its enum."""
        props = self._movies_props()
        big = [f"Genre Number {i}" for i in range(ai_engine.MAX_ENUM_OPTIONS + 1)]
        monkeypatch.setitem(props["Genres"], "_runtime_options", big)
        schema = get_gemini_schema("movies")
        assert "enum" not in schema["properties"]["Genres"]["items"]

    def test_enum_kept_at_cap(self, monkeypatch):
        props = self._movies_props()
        small = [f"Genre Number {i}" for i in range(ai_engine.MAX_ENUM_OPTIONS)]
        monkeypatch.setitem(props["Genres"], "_runtime_options", small)
        schema = get_gemini_schema("movies")
        assert schema["properties"]["Genres"]["items"]["enum"] == small

    def test_prompt_omits_oversized_option_lists(self, monkeypatch):
        """The prompt's valid-options dump is capped too (2k names ≈ 30k wasted tokens)."""
        props = self._movies_props()
        big = [f"Actor Number {i}" for i in range(ai_engine.MAX_ENUM_OPTIONS + 1)]
        monkeypatch.setitem(props["Famous Cast Members"], "_runtime_options", big)
        prompt = generate_extraction_prompt("movies", "some movie")
        assert "Actor Number 5" not in prompt

    def test_prompt_keeps_small_option_lists(self, monkeypatch):
        props = self._movies_props()
        monkeypatch.setitem(props["Genres"], "_runtime_options", ["Sci-Fi", "Drama"])
        prompt = generate_extraction_prompt("movies", "some movie")
        assert "Sci-Fi" in prompt
