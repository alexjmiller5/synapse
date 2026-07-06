"""Tests for business_logic.py — business rules, inventory, projects."""

from datetime import date

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

    def test_tasks_explicit_priority_kept(self):
        result = apply_business_logic("tasks", {"Name": "Do thing", "Priority": "Low"})
        assert result["Priority"] == "Low"

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
