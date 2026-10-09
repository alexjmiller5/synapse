"""Tests for schemas.py — static JSON schema definitions."""

from core.schemas import PARSER_SCHEMA, CATEGORY_SCHEMA_CLASSIFY
from core.config import CATEGORIES


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

    def test_every_category_and_nothing_else_is_classifiable(self):
        enum_values = CATEGORY_SCHEMA_CLASSIFY["properties"]["category"]["enum"]
        assert enum_values == list(CATEGORIES)
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


class TestPromptGuards:
    """CI-run regression guards for prompt-only fixes that no unit test would
    otherwise touch (their behavior tests live in the integration suite)."""

    def test_fun_activities_location_options_come_from_the_catalog(self):
        from core.ai_engine import get_gemini_schema

        schema = get_gemini_schema("fun-activities")
        assert "Lakeport" in schema["properties"]["Location"]["enum"]

    def test_place_tags_substituted_into_instructions(self, monkeypatch):
        """{place_tags} in a tasks instruction renders the configured list."""
        from core.ai_engine import generate_extraction_prompt

        monkeypatch.setitem(CATEGORIES["tasks"], "place_tags", ["Lake House"])
        prompt = generate_extraction_prompt("tasks", "fix the dock lines")
        assert '["Lake House"]' in prompt
        assert "{place_tags}" not in prompt

    def test_movies_tags_instruction_mentions_all_time_favorite(self):
        instr = CATEGORIES["movies"]["properties"]["Tags"]["instruction"]
        assert "all time favorite" in instr.lower()

    def test_media_categories_never_send_derived_columns(self):
        """Title/year/genres/cast are derived on the hub from the resolved id;
        Synapse extracts a Title only to resolve that id."""
        for category in ("movies", "tv-shows"):
            stanza = CATEGORIES[category]
            assert "columns" not in stanza
            assert set(stanza["capture_columns"].values()) <= {
                "status",
                "tags",
                "date_watched",
                "note",
            }

    def test_ideas_hobby_context_maps_to_hobby_tag(self):
        """A fun idea with no business case is Someday + Hobby, never Canceled."""
        instr = CATEGORIES["ideas"]["properties"]["Tags"]["instruction"].lower()
        for cue in ("hobby", "for fun", "no business case"):
            assert cue in instr

    def test_movies_status_priority_keywords(self):
        instr = CATEGORIES["movies"]["properties"]["Status"]["instruction"]
        assert "priority movie" in instr.lower()
        assert "need to watch" in instr.lower()

    def test_tv_status_instruction_uses_tv_names_and_is_unambiguous(self):
        instr = CATEGORIES["tv-shows"]["properties"]["Status"]["instruction"]
        # the catalog's partial-watch word is "Watched Parts" for movies and TV
        # alike; "Watched Some" is not an option and the hub rejects it
        assert "Watched Some" not in instr
        assert "Watched Parts" in instr
        # "must watch" must map to exactly one status (Priority, matching movies)
        assert "Must watch [title]" not in instr
        assert "must watch" in instr.lower() and "Priority" in instr

    def test_youtube_status_need_to_watch_is_priority(self):
        instr = CATEGORIES["youtube-videos"]["properties"]["Status"]["instruction"]
        assert "need to watch" in instr.lower()
        assert "Priority" in instr

    def test_tasks_due_date_resolves_bare_month(self):
        """A bare month name must resolve to its next occurrence, never January."""
        instr = CATEGORIES["tasks"]["properties"]["Due Date"]["instruction"]
        assert "BARE MONTH" in instr
        assert "NEVER default a bare month to January" in instr

    def test_task_fields_never_include_ai_columns(self):
        """Only the user sets AI Ready / AI Completed, and the AI Title column is gone."""
        props = CATEGORIES["tasks"]["properties"]
        assert not {"AI Ready", "AI Completed", "AI Title"} & set(props)
