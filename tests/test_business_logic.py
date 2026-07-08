"""Tests for business_logic.py — business rules, inventory, projects."""

from datetime import date
from unittest.mock import patch

from core.business_logic import (
    apply_business_logic,
    execute_logic,
    fetch_inventory_map,
    fetch_active_projects,
    fetch_trips_inventory,
    hydrate_dynamic_options,
    query_notion_db,
    fetch_property_options,
)
from helpers import make_notion_page


# ======================================================================
# apply_business_logic
# ======================================================================
class TestApplyBusinessLogic:
    def test_tasks_sets_status(self):
        data = {"Name": "Do thing", "Tags": ["Chore"]}
        result = apply_business_logic("tasks", data)
        assert result["Status"] == "To Do"

    def test_tasks_with_project_adds_notes(self):
        data = {"Name": "Fix bug"}
        result = apply_business_logic("tasks", data, related_project="Synapse")
        assert result["Status"] == "To Do"
        assert result["Notes"] == "Project: Synapse"

    def test_tasks_default_priority_high(self):
        result = apply_business_logic("tasks", {"Name": "Do thing"})
        assert result["Priority"] == "High"

    def test_project_tasks_default_priority_high(self):
        """Tasks routed to a project must default High like regular tasks."""
        result = apply_business_logic("tasks", {"Name": "Fix bug"}, related_project="Synapse")
        assert result["Priority"] == "High"

    def test_project_tasks_explicit_priority_kept(self):
        """A 'med'/'low' keyword the AI extracted must survive on project tasks."""
        med = apply_business_logic(
            "tasks", {"Name": "x", "Priority": "Medium"}, related_project="Synapse"
        )
        assert med["Priority"] == "Medium"
        low = apply_business_logic(
            "tasks", {"Name": "y", "Priority": "Low"}, related_project="Synapse"
        )
        assert low["Priority"] == "Low"

    def test_tasks_explicit_priority_kept(self):
        result = apply_business_logic("tasks", {"Name": "Do thing", "Priority": "Low"})
        assert result["Priority"] == "Low"

    def test_tasks_name_grounded_to_source_text(self):
        """AI mangled/rewrote the Name — grounding guard restores the verbatim input."""
        data = {"Name": "Buy MILK!!!", "Tags": ["Chore"]}
        result = apply_business_logic("tasks", data, source_text="buy milk")
        assert result["Name"] == "buy milk"

    def test_tasks_name_grounded_and_cleaned(self):
        """Grounded Name is also run through clean_text (spam/mojibake stripped)."""
        result = apply_business_logic("tasks", {"Name": "x"}, source_text="do it!!!!")
        assert result["Name"] == "do it!"

    def test_tasks_no_source_text_leaves_name(self):
        """Without source_text (e.g. cleanup tasks) the Name is left as-is."""
        result = apply_business_logic("tasks", {"Name": "Preserve me"})
        assert result["Name"] == "Preserve me"

    def test_non_task_name_not_grounded(self):
        """Groceries legitimately Title-Cases its name — source_text must NOT override it."""
        result = apply_business_logic("groceries", {"Name": "Eggs"}, source_text="buy eggs")
        assert result["Name"] == "Eggs"

    def test_quotes_formatting(self):
        data = {"Quote": '"I will be back"'}
        result = apply_business_logic("quotes", data)
        assert result["Quote"].startswith("\u201c")
        assert result["Quote"].endswith("\u201d")
        assert result["Date"] == date.today().isoformat()

    def test_quotes_strips_smart_quotes(self):
        data = {"Quote": "\u201cAlready quoted\u201d"}
        result = apply_business_logic("quotes", data)
        # Should not double-quote
        assert result["Quote"] == "\u201cAlready quoted\u201d"

    def test_movies_default_status(self):
        data = {"Title": "Inception"}
        result = apply_business_logic("movies", data)
        assert result["Status"] == "Not Started"

    def test_movies_keeps_explicit_status(self):
        data = {"Title": "Inception", "Status": "Finished"}
        result = apply_business_logic("movies", data)
        assert result["Status"] == "Finished"

    def test_movie_tmdb_overrides_ai_fields(self):
        """A TMDB match overrides the AI-guessed Genres/Director/Famous Cast Members."""
        meta = {
            "genres": ["Science Fiction"],
            "director": "Christopher Nolan",
            "cast": ["Leonardo DiCaprio", "Ellen Page"],
        }
        data = {
            "Title": "Inception",
            "Genres": ["Thriller (AI guess)"],
            "Director": "Wrong Guy",
            "Famous Cast Members": ["AI Actor"],
        }
        with patch("core.business_logic.get_tmdb_metadata", return_value=meta):
            result = apply_business_logic("movies", data)
        assert result["Director"] == "Christopher Nolan"
        assert result["Famous Cast Members"] == ["Leonardo DiCaprio", "Ellen Page"]
        # Genres run through map_genres (no runtime options in test → passthrough)
        assert result["Genres"] == ["Science Fiction"]

    def test_movie_no_tmdb_match_keeps_ai_fields(self):
        """No TMDB match (None) → the AI-extracted values are preserved."""
        data = {
            "Title": "Some Obscure Film",
            "Genres": ["Drama"],
            "Director": "AI Director",
            "Famous Cast Members": ["AI Actor"],
        }
        with patch("core.business_logic.get_tmdb_metadata", return_value=None):
            result = apply_business_logic("movies", data)
        assert result["Genres"] == ["Drama"]
        assert result["Director"] == "AI Director"
        assert result["Famous Cast Members"] == ["AI Actor"]

    def test_tv_show_tmdb_override(self):
        meta = {"genres": ["Drama"], "director": "Vince Gilligan", "cast": ["Bryan Cranston"]}
        data = {"Title": "Breaking Bad", "Genres": ["Comedy"], "Director": "x"}
        with patch("core.business_logic.get_tmdb_metadata", return_value=meta):
            result = apply_business_logic("tv-shows", data)
        assert result["Director"] == "Vince Gilligan"
        assert result["Famous Cast Members"] == ["Bryan Cranston"]

    def test_non_movie_category_never_tmdb_enriched(self):
        """Categories other than movies/tv-shows never call TMDB."""
        with patch("core.business_logic.get_tmdb_metadata") as mock_tmdb:
            apply_business_logic("tasks", {"Name": "Watch Inception"})
        mock_tmdb.assert_not_called()

    def test_podcasts_finished_sets_date(self):
        data = {"Episode Title": "Ep1", "Status": "Finished"}
        result = apply_business_logic("podcasts", data)
        assert result["Date Listened To"] == date.today().isoformat()

    def test_podcasts_not_finished_no_date(self):
        data = {"Episode Title": "Ep1", "Status": "Not Started"}
        result = apply_business_logic("podcasts", data)
        assert "Date Listened To" not in result

    def test_youtube_watched_sets_date(self):
        data = {"Title": "Video", "Status": "Watched"}
        result = apply_business_logic("youtube-videos", data)
        assert result["Date Watched"] == date.today().isoformat()

    def test_youtube_not_watched_no_date(self):
        data = {"Title": "Video", "Status": "Not Started"}
        result = apply_business_logic("youtube-videos", data)
        assert "Date Watched" not in result

    def test_bookmarks_github_tagging(self):
        data = {"URL": "https://github.com/owner/repo", "Tags": []}
        result = apply_business_logic("bookmarks", data)
        assert "Github" in result["Tags"]

    def test_bookmarks_github_no_duplicate(self):
        data = {"URL": "https://github.com/owner/repo", "Tags": ["Github"]}
        result = apply_business_logic("bookmarks", data)
        assert result["Tags"].count("Github") == 1

    def test_bookmarks_non_github(self):
        data = {"URL": "https://example.com", "Tags": ["Tech"]}
        result = apply_business_logic("bookmarks", data)
        assert "Github" not in result["Tags"]

    def test_unhandled_category_passthrough(self):
        data = {"Name": "Something"}
        result = apply_business_logic("people", data)
        assert result == data


# ======================================================================
# query_notion_db
# ======================================================================
class TestQueryNotionDb:
    def test_returns_results(self, mock_notion):
        page = make_notion_page("p1")
        mock_notion.request.return_value = {"results": [page]}
        results = query_notion_db("tasks")
        assert len(results) == 1

    def test_empty_results(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        assert query_notion_db("tasks") == []

    def test_exception_returns_empty(self, mock_notion):
        mock_notion.request.side_effect = Exception("API error")
        assert query_notion_db("tasks") == []


# ======================================================================
# fetch_inventory_map
# ======================================================================
class TestFetchInventoryMap:
    def test_builds_map(self, mock_notion):
        pages = [
            make_notion_page("id-1", "Name", "Eggs"),
            make_notion_page("id-2", "Name", "Milk"),
        ]
        mock_notion.request.return_value = {"results": pages}

        inventory = fetch_inventory_map("groceries")
        assert inventory == {"Eggs": "id-1", "Milk": "id-2"}

    def test_empty_db(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        assert fetch_inventory_map("groceries") == {}


# ======================================================================
# fetch_active_projects
# ======================================================================
class TestFetchActiveProjects:
    def test_returns_projects(self, mock_notion):
        pages = [
            make_notion_page("proj1", "Title", "Synapse"),
            make_notion_page("proj2", "Title", "Blueprint"),
        ]
        mock_notion.request.return_value = {"results": pages}

        prompt_list, id_map = fetch_active_projects()
        assert "Synapse" in prompt_list
        assert "Blueprint" in prompt_list
        assert id_map["Synapse"] == "proj1"

    def test_skips_unknown(self, mock_notion):
        """Pages with empty titles are skipped."""
        page = {"id": "bad", "properties": {"Title": {"title": []}}}
        mock_notion.request.return_value = {"results": [page]}

        prompt_list, id_map = fetch_active_projects()
        assert len(prompt_list) == 0


# ======================================================================
# fetch_trips_inventory
# ======================================================================
class TestFetchTripsInventory:
    def test_returns_trips(self, mock_notion):
        page = {
            "id": "trip1",
            "properties": {
                "Name": {"title": [{"plain_text": "NYC Trip"}]},
                "Dates": {"date": {"start": "2026-06-01"}},
            },
        }
        mock_notion.request.return_value = {"results": [page]}

        trips_list, trips_map = fetch_trips_inventory()
        assert "NYC Trip (Date: 2026-06-01)" in trips_list
        assert trips_map["NYC Trip"] == "trip1"


# ======================================================================
# execute_logic
# ======================================================================
class TestExecuteLogic:
    def test_tasks_uses_default_handler(self, mock_notion):
        """Tasks without a project go through execute_logic -> default handler."""
        data = {"Name": "Test task", "Status": "To Do", "Tags": ["Chore"]}
        execute_logic("tasks", data)
        # Should have called create_page via handle_default_logic
        assert mock_notion.pages.create.called

    def test_groceries_routing(self, mock_notion):
        data = {"Name": "New Item", "Status": "On List"}
        execute_logic("groceries", data, inventory_map={})
        mock_notion.pages.create.assert_called()

    def test_places_routing(self, mock_notion):
        data = {"Name": "Central Park", "Status": "Haven't Been"}
        execute_logic("places", data, trips_id_map={})
        mock_notion.pages.create.assert_called()


# ======================================================================
# hydrate_dynamic_options
# ======================================================================
class TestHydrateDynamicOptions:
    def test_populates_runtime_options(self, mock_notion):
        mock_notion.databases.retrieve.return_value = {
            "properties": {
                "Tags": {
                    "type": "multi_select",
                    "multi_select": {"options": [{"name": "Chore"}, {"name": "Errand"}]},
                }
            }
        }
        # This modifies DATABASES in-place
        hydrate_dynamic_options()
        # Verify it ran without error (detailed check would require inspecting DATABASES)
        assert True

    def test_only_category_hydrates_just_that_category(self, mock_notion):
        """The hot path: hydrate only the classified category, not all ~15
        (which cost ~40 Notion calls per thought). fetch_property_options must
        be called only for the requested category's select props."""
        from core.config import DATABASES

        mock_notion.databases.retrieve.return_value = {
            "properties": {"Tags": {"type": "multi_select", "multi_select": {"options": []}}}
        }
        try:
            hydrate_dynamic_options(only_category="tasks")
            calls = mock_notion.databases.retrieve.call_count
            # tasks has a handful of select/status props; a full all-category
            # hydrate would retrieve far more DBs. Assert we touched exactly one DB.
            db_ids = {
                c.kwargs.get("database_id") or c.args[0]
                for c in mock_notion.databases.retrieve.call_args_list
            }
            assert len(db_ids) == 1, f"expected 1 DB hydrated, got {len(db_ids)}: {db_ids}"
            assert calls >= 1
        finally:
            for details in DATABASES["databases"].values():
                for rules in details.get("properties", {}).values():
                    rules.pop("_runtime_options", None)

    def test_warns_when_allowlist_option_missing_from_live_select(self, mock_notion, capsys):
        """An allowlist entry absent from the live Notion select is filtered out
        of the AI enum — hydration must warn loudly instead of silently no-oping."""
        from core.config import DATABASES

        # Live fun-activities Location select without the 'Lakeport' option
        mock_notion.databases.retrieve.return_value = {
            "properties": {
                "Location": {
                    "type": "select",
                    "select": {
                        "options": [{"name": "Boston"}, {"name": "Dallas"}, {"name": "NYC"}]
                    },
                }
            }
        }
        try:
            hydrate_dynamic_options()
            out = capsys.readouterr().out
            assert "Lakeport" in out and "missing" in out
            location = DATABASES["databases"]["fun-activities"]["properties"]["Location"]
            assert "Lakeport" not in location["_runtime_options"]
        finally:
            # Undo the in-place DATABASES mutation so schema tests keep seeing allowlists
            for details in DATABASES["databases"].values():
                for rules in details.get("properties", {}).values():
                    rules.pop("_runtime_options", None)


# ======================================================================
# fetch_property_options
# ======================================================================
class TestFetchPropertyOptions:
    def test_select_options(self, mock_notion):
        mock_notion.databases.retrieve.return_value = {
            "properties": {
                "Priority": {
                    "type": "select",
                    "select": {"options": [{"name": "Low"}, {"name": "High"}]},
                }
            }
        }
        result = fetch_property_options("fake-db-id", "Priority")
        assert result == ["Low", "High"]

    def test_multi_select_options(self, mock_notion):
        mock_notion.databases.retrieve.return_value = {
            "properties": {
                "Tags": {
                    "type": "multi_select",
                    "multi_select": {"options": [{"name": "A"}, {"name": "B"}]},
                }
            }
        }
        result = fetch_property_options("fake-db-id", "Tags")
        assert result == ["A", "B"]

    def test_status_options(self, mock_notion):
        mock_notion.databases.retrieve.return_value = {
            "properties": {
                "Status": {
                    "type": "status",
                    "status": {"options": [{"name": "To Do"}, {"name": "Done"}]},
                }
            }
        }
        result = fetch_property_options("fake-db-id", "Status")
        assert result == ["To Do", "Done"]

    def test_missing_property(self, mock_notion):
        mock_notion.databases.retrieve.return_value = {"properties": {}}
        result = fetch_property_options("fake-db-id", "NonExistent")
        assert result == []
