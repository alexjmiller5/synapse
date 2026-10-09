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
        assert '"tasks"' in prompt and '"groceries"' in prompt and '"movies"' in prompt

    def test_includes_projects(self):
        prompt = generate_classification_prompt("Synapse, Blueprint")
        assert "Synapse" in prompt
        assert "Blueprint" in prompt

    def test_only_capture_categories_are_offered(self):
        """Tables written only as a side effect (channels, the execution log) and
        retired routes are not classification targets."""
        prompt = generate_classification_prompt("None")
        for gone in ("youtube-channels", "logs", "places", "trips", "quotes"):
            assert f'"{gone}"' not in prompt

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

    def test_unknown_category_is_an_error(self):
        with pytest.raises(KeyError):
            generate_extraction_prompt("nonexistent", "text")

    def test_ai_ready_absent_from_prompt(self):
        prompt = generate_extraction_prompt("tasks", "have ai do this")
        assert "AI Ready" not in prompt

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

    def test_includes_user_context(self):
        prompt = generate_extraction_prompt("tasks", "Do thing", user_context="urgent due friday")
        assert "USER EXPLICIT CONTEXT" in prompt
        assert "urgent due friday" in prompt

    def test_task_status_is_set_by_synapse_not_extracted(self):
        prompt = generate_extraction_prompt("tasks", "test")
        assert not any(line.strip().startswith("- `Status`:") for line in prompt.split("\n"))


# ======================================================================
# The catalog drives options, meanings, defaults and rules
# ======================================================================
class TestCatalogDrivenPrompt:
    def test_unnarrowed_options_come_from_the_catalog_with_their_meanings(self):
        prompt = generate_extraction_prompt("movies", "need to watch dune")
        assert "--- VALID STATUS (STRICT) ---" in prompt
        assert '- "Priority": Need to watch / must watch / dying to see' in prompt
        assert '"Mockumentary"' in prompt  # the catalog's spelling, not a stale copy

    def test_an_allowlist_narrows_to_catalog_values_and_its_instruction_owns_meaning(self):
        prompt = generate_extraction_prompt("groceries", "buy milk")
        assert '--- VALID STATUS (STRICT) ---\n["On List", "Don\'t Have", "Have"]' in prompt
        assert "In Cart" not in prompt

    def test_catalog_default_is_stated_for_a_field_the_row_needs(self):
        prompt = generate_extraction_prompt("groceries", "buy milk")
        assert '- `Status`: (default when the text gives none: "On List")' in prompt

    def test_capture_fields_get_no_default_so_a_neutral_mention_resets_nothing(self):
        prompt = generate_extraction_prompt("movies", "dune")
        assert "default when the text gives none" not in prompt

    def test_enforced_table_rules_are_stated(self):
        prompt = generate_extraction_prompt("bookmarks", "https://example.com")
        assert "--- STORE RULES (a row that breaks one is rejected) ---" in prompt
        assert "- description never ends with a period." in prompt
        assert "STORE RULES" not in generate_extraction_prompt("tasks", "x")

    def test_allowlist_values_the_catalog_lacks_are_dropped(self, monkeypatch):
        from core.config import CATEGORIES

        status = CATEGORIES["groceries"]["properties"]["Status"]
        monkeypatch.setitem(status, "allowlist", ["On List", "Gone Forever"])
        schema = get_gemini_schema("groceries")
        assert schema["properties"]["Status"]["enum"] == ["On List"]

    def test_a_column_the_catalog_does_not_know_keeps_its_allowlist(self, monkeypatch):
        from core import catalog

        monkeypatch.setattr(catalog, "table", lambda name: {"columns": {}, "rules": []})
        assert get_gemini_schema("groceries")["properties"]["Status"]["enum"] == [
            "On List",
            "Don't Have",
            "Have",
        ]

    def test_task_fields_resolve_through_the_workflow_binding(self):
        table, spec = ai_engine.fields("tasks")
        assert table == "tasks"
        tags = next(f for f in spec if f["name"] == "Tags")
        assert tags["type"] == "multi_select" and "Chore" in [o["v"] for o in tags["options"]]


# ======================================================================
# get_gemini_schema
# ======================================================================
class TestGetGeminiSchema:
    def test_tasks_schema(self):
        schema = get_gemini_schema("tasks")
        assert schema["type"] == "object"
        assert {"Name", "Tags", "Due Date", "Priority", "Links"} == set(schema["properties"])
        assert set(schema["required"]) == {"Name", "Tags", "Due Date"}

    def test_multi_select_is_an_array_of_catalog_options(self):
        tags = get_gemini_schema("ideas")["properties"]["Tags"]
        assert tags["type"] == "array"
        assert "Hobby" in tags["items"]["enum"]

    def test_select_options_come_from_the_catalog(self):
        status = get_gemini_schema("movies")["properties"]["Status"]
        assert status == {
            "type": "string",
            "enum": [
                "Priority",
                "Not Started",
                "In Progress",
                "Finished",
                "Watched Parts",
                "Gave Up",
            ],
        }

    def test_text_columns_are_open(self):
        props = get_gemini_schema("podcasts")["properties"]
        assert props["Podcast Name"] == {"type": "string"}
        assert props["Producer"] == {"type": "string"}

    def test_fields_without_a_column_keep_synapse_vocabulary(self):
        intent = get_gemini_schema("movies")["properties"]["Capture Intent"]
        assert intent["enum"] == ["save", "record_consumption", "none"]

    def test_catalog_required_columns_are_required(self):
        schema = get_gemini_schema("groceries")
        assert set(schema["required"]) == {"Name", "Category", "Status"}

    def test_capture_fields_are_never_forced_even_when_the_column_is_required(self):
        schema = get_gemini_schema("podcasts")
        assert "Status" not in schema["required"]
        assert {"Episode Title", "Podcast Name", "URL"} <= set(schema["required"])

    def test_bookmarks_schema(self):
        schema = get_gemini_schema("bookmarks")
        assert set(schema["properties"]) == {"Description", "Title", "URL", "Tags"}
        assert set(schema["required"]) == {"Description", "Title", "URL"}

    def test_tasks_schema_has_no_ai_ready(self):
        """Synapse must never tick 'AI Ready' - only the user sets it, by hand."""
        schema = get_gemini_schema("tasks")
        assert "AI Ready" not in schema["properties"]


# ======================================================================
# Enum size cap (Gemini 400 INVALID_ARGUMENT on huge enums)
# ======================================================================
class TestEnumCap:
    """Gemini rejects response schemas whose enums exceed its constrained-decoding
    grammar limit (~150 distinct real-world names); a catalog option list past
    MAX_ENUM_OPTIONS drops its enum instead of 400ing every capture."""

    def _genres(self, monkeypatch, values):
        from core import catalog

        real = catalog.table

        def table(name):
            out = real(name)
            if name == "podcast_episodes":
                out = {**out, "columns": dict(out["columns"])}
                out["columns"]["genres"] = {**out["columns"]["genres"], "options": values}
            return out

        monkeypatch.setattr(catalog, "table", table)

    def test_enum_dropped_above_cap(self, monkeypatch):
        big = [{"v": f"Genre Number {i}"} for i in range(ai_engine.MAX_ENUM_OPTIONS + 1)]
        self._genres(monkeypatch, big)
        assert "enum" not in get_gemini_schema("podcasts")["properties"]["Genres"]["items"]

    def test_enum_kept_at_cap(self, monkeypatch):
        small = [{"v": f"Genre Number {i}"} for i in range(ai_engine.MAX_ENUM_OPTIONS)]
        self._genres(monkeypatch, small)
        enum = get_gemini_schema("podcasts")["properties"]["Genres"]["items"]["enum"]
        assert enum == [o["v"] for o in small]

    def test_prompt_omits_oversized_option_lists(self, monkeypatch):
        big = [{"v": f"Genre Number {i}"} for i in range(ai_engine.MAX_ENUM_OPTIONS + 1)]
        self._genres(monkeypatch, big)
        assert "Genre Number 5" not in generate_extraction_prompt("podcasts", "some podcast")
