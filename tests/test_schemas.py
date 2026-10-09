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
        allow = DATABASES["databases"]["movies"]["properties"]["Tags"]["allowlist"]
        # one Favorite tier: the old spellings must never be offered again
        assert "Favorite" in allow
        assert "All-time Favorite" not in allow
        assert "Best Movies" not in allow

    def test_media_categories_are_hub_backed_not_notion(self):
        """movies/tv-shows write to soma: they carry a hub_table and NO db_id
        (a db_id would put them back on the Notion hydrate/validate/write paths)."""
        for cat, table in (("movies", "movies"), ("tv-shows", "tv_shows")):
            stanza = DATABASES["databases"][cat]
            assert stanza["hub_table"] == table
            assert "db_id" not in stanza

    def test_media_derived_properties_removed(self):
        """Genres/Director/Famous Cast Members are TMDB-derived ON THE HUB now -
        extracting AI guesses for them would be rejected as unprovenanced."""
        for cat in ("movies", "tv-shows"):
            props = DATABASES["databases"][cat]["properties"]
            assert not {"Genres", "Director", "Famous Cast Members"}.intersection(props)

    def test_media_tags_allowlists_match_the_soma_catalog(self):
        assert set(DATABASES["databases"]["movies"]["properties"]["Tags"]["allowlist"]) == {
            "Favorite",
            "Sequel",
            "Prequel",
            "Studio Ghibli",
            "LS477",
            "Sad",
            "Coming-of-age",
            "Animé",
            "Mocumentary",
            "Spanish",
            "Sport",
            "Concert",
            "Cult Classic",
        }
        assert set(DATABASES["databases"]["tv-shows"]["properties"]["Tags"]["allowlist"]) == {
            "Favorite",
            "Classic",
            "Sequel",
            "Prequel",
            "Animé",
            "Dystopia",
            "Mocumentary",
            "Spanish",
            "Sport",
            "Game-Show",
            "Medical",
            "Video Game",
            "Sitcom",
            "Educational",
        }

    def test_movies_status_priority_keywords(self):
        instr = DATABASES["databases"]["movies"]["properties"]["Status"]["instruction"]
        assert "priority movie" in instr.lower()
        assert "need to watch" in instr.lower()

    def test_tv_status_allowlist_matches_live_options(self):
        """The allowlist is exactly the tv_shows.status options in the soma
        catalog; any other word is rejected by the hub."""
        allow = DATABASES["databases"]["tv-shows"]["properties"]["Status"]["allowlist"]
        assert set(allow) == {
            "Priority",
            "Not Started",
            "Watched Parts",
            "In Progress",
            "Finished",
            "Gave Up",
        }

    def test_tv_status_instruction_uses_tv_names_and_is_unambiguous(self):
        instr = DATABASES["databases"]["tv-shows"]["properties"]["Status"]["instruction"]
        # the catalog's partial-watch word is "Watched Parts" for movies and TV
        # alike; "Watched Some" is not an option and the hub rejects it
        assert "Watched Some" not in instr
        assert "Watched Parts" in instr
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
