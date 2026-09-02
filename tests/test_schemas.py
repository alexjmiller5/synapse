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

    def test_fun_activities_location_enum_includes_lakeport(self):
        from core.ai_engine import get_gemini_schema

        schema = get_gemini_schema("fun-activities")
        assert "Lakeport" in schema["properties"]["Location"]["enum"]

    def test_location_allowlist_env_override(self, monkeypatch):
        """NOTION_FUN_ACTIVITIES_LOCATIONS replaces the committed example cities."""
        from core.config import apply_env_overrides

        dbs = {
            "databases": {
                "fun-activities": {"properties": {"Location": {"allowlist": ["Lakeport"]}}}
            }
        }
        monkeypatch.delenv("NOTION_FUN_ACTIVITIES_LOCATIONS", raising=False)
        loc = apply_env_overrides(dbs)["databases"]["fun-activities"]["properties"]["Location"]
        assert loc["allowlist"] == ["Lakeport"]

        monkeypatch.setenv("NOTION_FUN_ACTIVITIES_LOCATIONS", "Boston, Springfield ,NYC")
        loc = apply_env_overrides(dbs)["databases"]["fun-activities"]["properties"]["Location"]
        assert loc["allowlist"] == ["Boston", "Springfield", "NYC"]

    def test_tasks_place_tags_env_override(self, monkeypatch):
        """NOTION_TASKS_PLACE_TAGS joins the tasks Tags allowlist and lands on the
        category as place_tags (consumed by {place_tags} in prompt instructions)."""
        from core.config import apply_env_overrides

        def fresh():
            return {"databases": {"tasks": {"properties": {"Tags": {"allowlist": ["Chore"]}}}}}

        monkeypatch.delenv("NOTION_TASKS_PLACE_TAGS", raising=False)
        tasks = apply_env_overrides(fresh())["databases"]["tasks"]
        assert "place_tags" not in tasks
        assert tasks["properties"]["Tags"]["allowlist"] == ["Chore"]

        monkeypatch.setenv("NOTION_TASKS_PLACE_TAGS", "Lake House, Hometown ")
        tasks = apply_env_overrides(fresh())["databases"]["tasks"]
        assert tasks["place_tags"] == ["Lake House", "Hometown"]
        assert tasks["properties"]["Tags"]["allowlist"] == ["Chore", "Lake House", "Hometown"]

    def test_place_tags_substituted_into_instructions(self, monkeypatch):
        """{place_tags} in a tasks instruction renders the configured list."""
        from core.ai_engine import generate_extraction_prompt
        from core.config import DATABASES

        monkeypatch.setitem(DATABASES["databases"]["tasks"], "place_tags", ["Lake House"])
        prompt = generate_extraction_prompt("tasks", "fix the dock lines")
        assert '["Lake House"]' in prompt
        assert "{place_tags}" not in prompt

    def test_task_ai_title_property_removed(self):
        """Alex deleted the 'AI Title' property from the Tasks DB — Synapse must
        no longer define or write it (otherwise every task write 400s)."""
        assert "AI Title" not in DATABASES["databases"]["tasks"]["properties"]

    def test_movies_tags_instruction_mentions_all_time_favorite(self):
        instr = DATABASES["databases"]["movies"]["properties"]["Tags"]["instruction"]
        assert "all time favorite" in instr.lower()
        assert (
            "All-time Favorite"
            in DATABASES["databases"]["movies"]["properties"]["Tags"]["allowlist"]
        )

    def test_movies_status_priority_keywords(self):
        instr = DATABASES["databases"]["movies"]["properties"]["Status"]["instruction"]
        assert "priority movie" in instr.lower()
        assert "need to watch" in instr.lower()

    def test_tv_status_allowlist_matches_live_options(self):
        """The live TV Shows DB has six statuses (verified 2026-09-01); an
        allowlist missing two makes them unreachable — hydration intersects
        with live options, it never adds."""
        allow = DATABASES["databases"]["tv-shows"]["properties"]["Status"]["allowlist"]
        assert set(allow) == {
            "Priority",
            "Not Started",
            "Watched Some",
            "In Progress",
            "Finished",
            "Gave Up",
        }

    def test_tv_status_instruction_uses_tv_names_and_is_unambiguous(self):
        instr = DATABASES["databases"]["tv-shows"]["properties"]["Status"]["instruction"]
        # "Watched Parts" is the MOVIES name; on TV the partial-watch status is
        # "Watched Some" — naming a non-existent status sends picks into the
        # hydration filter and the field comes back empty
        assert "Watched Parts" not in instr
        assert "Watched Some" in instr
        # "must watch" must map to exactly one status (Priority, matching
        # movies) — the old text routed "Must watch [title]" to Not Started in
        # one clause and "must watch" to Priority in another
        assert "Must watch [title]" not in instr
        assert "must watch" in instr.lower() and "Priority" in instr

    def test_youtube_status_need_to_watch_is_priority(self):
        instr = DATABASES["databases"]["youtube-videos"]["properties"]["Status"]["instruction"]
        assert "need to watch" in instr.lower()
        assert "Priority" in instr

    def test_tasks_due_date_resolves_bare_month(self):
        """A bare month name must resolve to its next occurrence, never January."""
        instr = DATABASES["databases"]["tasks"]["properties"]["Due Date"]["instruction"]
        assert "BARE MONTH" in instr
        assert "NEVER default a bare month to January" in instr
