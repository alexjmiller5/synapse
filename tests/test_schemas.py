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

    def test_project_action_removed(self):
        """Project notes are gone — a matched project is always a task."""
        assert "project_action" not in CATEGORY_SCHEMA_CLASSIFY["properties"]

    def test_related_project_field(self):
        assert "related_project" in CATEGORY_SCHEMA_CLASSIFY["properties"]
        assert CATEGORY_SCHEMA_CLASSIFY["properties"]["related_project"]["type"] == "string"


class TestYamlFixGuards:
    """CI-run regression guards for YAML-only fixes that no unit test would
    otherwise touch (their behavior tests live in the integration suite)."""

    def test_fun_activities_location_enum_includes_westport(self):
        from core.ai_engine import get_gemini_schema

        schema = get_gemini_schema("fun-activities")
        assert "Lakeport" in schema["properties"]["Location"]["enum"]

    def test_task_ai_title_property_removed(self):
        """Alex deleted the 'AI Title' property from the Tasks DB — Synapse must
        no longer define or write it (otherwise every task write 400s)."""
        assert "AI Title" not in DATABASES["databases"]["tasks"]["properties"]

    def test_movies_tags_instruction_mentions_all_time_favorite(self):
        instr = DATABASES["databases"]["movies"]["properties"]["Tags"]["instruction"]
        assert "all time favorite" in instr.lower()
        assert "All Time Favorite" in DATABASES["databases"]["movies"]["properties"]["Tags"]["allowlist"]

    def test_movies_status_priority_keywords(self):
        instr = DATABASES["databases"]["movies"]["properties"]["Status"]["instruction"]
        assert "priority movie" in instr.lower()
        assert "need to watch" in instr.lower()

    def test_youtube_status_need_to_watch_is_priority(self):
        instr = DATABASES["databases"]["youtube-videos"]["properties"]["Status"]["instruction"]
        assert "need to watch" in instr.lower()
        assert "Priority" in instr

    def test_tasks_due_date_resolves_bare_month(self):
        """A bare month name must resolve to its next occurrence, never January."""
        instr = DATABASES["databases"]["tasks"]["properties"]["Due Date"]["instruction"]
        assert "BARE MONTH" in instr
        assert "NEVER default a bare month to January" in instr
