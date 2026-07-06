"""Tests for schemas.py — static JSON schema definitions."""

from core.schemas import PARSER_SCHEMA, CATEGORY_SCHEMA_CLASSIFY
from core.config import DATABASES


class TestParserSchema:
    def test_is_array(self):
        assert PARSER_SCHEMA["type"] == "array"

    def test_items_have_core_text(self):
        props = PARSER_SCHEMA["items"]["properties"]
        assert "core_text" in props
        assert props["core_text"]["type"] == "string"

    def test_items_have_context_notes(self):
        props = PARSER_SCHEMA["items"]["properties"]
        assert "context_notes" in props

    def test_core_text_required(self):
        assert "core_text" in PARSER_SCHEMA["items"]["required"]


class TestCategorySchemaClassify:
    def test_is_object(self):
        assert CATEGORY_SCHEMA_CLASSIFY["type"] == "object"

    def test_category_has_enum(self):
        cat_prop = CATEGORY_SCHEMA_CLASSIFY["properties"]["category"]
        assert "enum" in cat_prop
        assert len(cat_prop["enum"]) > 0

    def test_non_helper_categories_present_helpers_excluded(self):
        dbs = DATABASES.get("databases", {})
        enum_values = CATEGORY_SCHEMA_CLASSIFY["properties"]["category"]["enum"]
        for cat, details in dbs.items():
            if details.get("helper"):
                assert cat not in enum_values, f"Helper DB leaked into classifier enum: {cat}"
            else:
                assert cat in enum_values, f"Missing category: {cat}"
        # The helper DBs behind the trips date bug must be unselectable.
        assert "trips" not in enum_values
        assert "logs" not in enum_values
        assert "youtube-channels" not in enum_values

    def test_category_required(self):
        assert "category" in CATEGORY_SCHEMA_CLASSIFY["required"]

    def test_project_action_enum(self):
        pa = CATEGORY_SCHEMA_CLASSIFY["properties"]["project_action"]
        assert pa["enum"] == ["task", "note"]

    def test_related_project_field(self):
        assert "related_project" in CATEGORY_SCHEMA_CLASSIFY["properties"]
        assert CATEGORY_SCHEMA_CLASSIFY["properties"]["related_project"]["type"] == "string"
