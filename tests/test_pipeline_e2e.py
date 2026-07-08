"""End-to-end pipeline tests — exercise run_pipeline() with mocked external services.

Each test sends realistic input through the full pipeline and verifies
the correct Notion API calls are made with proper data.
"""

from unittest.mock import patch

from core.pipeline import run_pipeline, run
from core.schemas import CATEGORY_SCHEMA_CLASSIFY
from helpers import make_gemini_response, make_notion_page


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _item(core_text, context_notes=""):
    return {"core_text": core_text, "context_notes": context_notes}


def _setup_classify_extract(mock_gemini, category, extracted, project=None):
    """Configure mock Gemini to return classification then extraction."""
    classify_resp = {"category": category}
    if project:
        classify_resp["related_project"] = project

    responses = [
        make_gemini_response(classify_resp),  # Classification
        make_gemini_response(extracted),       # Extraction
    ]
    mock_gemini.models.generate_content.side_effect = responses


def _setup_classify_extract_mobile(mock_gemini, category, extracted, mobile_compat=False):
    """Configure for tasks with mobile compatibility check (3 Gemini calls)."""
    responses = [
        make_gemini_response({"category": category}),
        make_gemini_response(extracted),
        make_gemini_response({"mobile_compatible": mobile_compat}),
    ]
    mock_gemini.models.generate_content.side_effect = responses


# ---------------------------------------------------------------------------
# Pipeline context defaults
# ---------------------------------------------------------------------------
DEFAULT_CTX = {
    "project_prompts": ["Synapse"],
    "project_id_map": {"Synapse": "synapse-project-id"},
    "inventory_map": {"Eggs": "eggs-id", "Milk": "milk-id"},
    "inventory_list": ["Eggs", "Milk"],
    "trips_list": ["NYC Trip (Date: 2026-06-01)"],
    "trips_id_map": {"NYC Trip": "nyc-trip-id"},
}


def _log_props(mock_notion):
    """Return the properties of the execution-log page create (has Raw Input)."""
    for call in mock_notion.pages.create.call_args_list:
        props = call.kwargs["properties"]
        if "Raw Input" in props:
            return props
    raise AssertionError("No execution log page was created")


def _run(item_data, **overrides):
    ctx = {**DEFAULT_CTX, **overrides}
    run_pipeline(
        item_data,
        ctx["project_prompts"],
        ctx["project_id_map"],
        ctx["inventory_map"],
        ctx["inventory_list"],
        ctx["trips_list"],
        ctx["trips_id_map"],
    )


# ======================================================================
# Task Tests
# ======================================================================
class TestTaskPipeline:
    def test_simple_task(self, mock_gemini, mock_notion):
        _setup_classify_extract_mobile(mock_gemini, "tasks", {
            "Name": "Update dating profile",
            "AI Title": "Update dating profile",
            "Tags": ["Chore"],
            "Due Date": "2026-03-29",
        })

        _run(_item("Update dating profile"))
        # Should create a task page + log outcome = 2 creates
        assert mock_notion.pages.create.call_count >= 1
        # Ordinary execution — log must NOT carry the project-append tag
        assert "Tags" not in _log_props(mock_notion)

    def test_task_with_context(self, mock_gemini, mock_notion):
        _setup_classify_extract_mobile(mock_gemini, "tasks", {
            "Name": "Cancel Uber One",
            "AI Title": "Cancel Uber One subscription",
            "Tags": ["Chore"],
            "Due Date": "2027-01-01",
        })

        _run(_item("Cancel Uber One", "Jan 1"))
        assert mock_notion.pages.create.called

    def test_task_mobile_compatible(self, mock_gemini, mock_notion):
        _setup_classify_extract_mobile(mock_gemini, "tasks", {
            "Name": "Text mom back",
            "AI Title": "Text mom back",
            "Tags": ["Chore"],
            "Due Date": "2026-03-29",
        }, mobile_compat=True)

        _run(_item("Text mom back"))
        # Verify the create call happened
        assert mock_notion.pages.create.called

    def test_task_not_mobile_compatible(self, mock_gemini, mock_notion):
        _setup_classify_extract_mobile(mock_gemini, "tasks", {
            "Name": "Fix production server",
            "AI Title": "Fix production server",
            "Tags": ["Work"],
            "Due Date": "2026-03-29",
        }, mobile_compat=False)

        _run(_item("Fix production server"))
        assert mock_notion.pages.create.called


# ======================================================================
# Deterministic task-context pre-check
# ======================================================================
class TestTaskContextPrecheck:
    def test_task_context_skips_classifier(self, mock_gemini, mock_notion):
        """Context containing the word 'task' classifies deterministically — no classify call."""
        # Only the extraction response is queued: a classification call would
        # consume it and break the sequence.
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({
                "Name": "Add the full x men series to my movies db",
                "AI Title": "Add X-Men series to movies DB",
                "Tags": ["Chore"],
                "Due Date": "2026-07-10",
            }),
        ]

        _run(_item("Add the full x men series to my movies db", "med prior task"))

        assert mock_gemini.models.generate_content.call_count == 1
        first_cfg = mock_gemini.models.generate_content.call_args_list[0].kwargs["config"]
        assert first_cfg.response_json_schema is not CATEGORY_SCHEMA_CLASSIFY
        assert mock_notion.pages.create.called

    def test_date_context_still_calls_classifier(self, mock_gemini, mock_notion):
        """A plain date context does NOT trigger the pre-check — classifier runs."""
        _setup_classify_extract_mobile(mock_gemini, "tasks", {
            "Name": "watch Eric Andre's new movie, little brother",
            "AI Title": "Watch Little Brother",
            "Tags": ["Chore"],
            "Due Date": "2026-06-26",
        })

        _run(_item("watch Eric Andre's new movie, little brother", "June 26"))

        assert mock_gemini.models.generate_content.call_count == 2
        first_cfg = mock_gemini.models.generate_content.call_args_list[0].kwargs["config"]
        assert first_cfg.response_json_schema is CATEGORY_SCHEMA_CLASSIFY
        assert mock_notion.pages.create.called


# ======================================================================
# Project Task/Note Tests
# ======================================================================
class TestProjectPipeline:
    def test_project_task(self, mock_gemini, mock_notion):
        """Task with project context creates a project-linked task."""
        responses = [
            make_gemini_response({
                "category": "tasks",
                "related_project": "Synapse",
            }),
            make_gemini_response({
                "Name": "Fix login bug",
                "Tags": ["Chore"],
                "Due Date": "2026-03-29",
            }),
            make_gemini_response({"mobile_compatible": False}),
        ]
        mock_gemini.models.generate_content.side_effect = responses

        _run(_item("Fix login bug", "Synapse"))
        # Should create task with project relation
        assert mock_notion.pages.create.called
        # Check that Project relation was added
        create_calls = mock_notion.pages.create.call_args_list
        task_create = create_calls[0]
        props = task_create.kwargs["properties"]
        assert "Project" in props
        # Project tasks default to High priority (like regular tasks)
        assert props["Priority"]["select"]["name"] == "High"
        # Execution log must be tagged as a project-append execution
        log_props = _log_props(mock_notion)
        assert log_props["Tags"]["multi_select"] == [{"name": "project-append"}]

    def test_task_context_links_project(self, mock_gemini, mock_notion):
        """Deterministic 'task' pre-check still links a referenced project instead
        of dropping it — even with 'task' in the context, no classifier call runs."""
        # Only the extraction response is queued (pre-check skips the classifier).
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({
                "Name": "Fix Synapse login bug",
                "Tags": ["Chore"],
                "Due Date": "2026-07-10",
            }),
        ]

        _run(_item("Fix Synapse login bug", "high priority task"))

        # Classifier was skipped (1 Gemini call = extraction only)
        assert mock_gemini.models.generate_content.call_count == 1
        # Task created with a Project relation to the matched 'Synapse' project
        props = mock_notion.pages.create.call_args_list[0].kwargs["properties"]
        assert props["Project"] == {"relation": [{"id": "synapse-project-id"}]}
        assert props["Priority"]["select"]["name"] == "High"
        assert _log_props(mock_notion)["Tags"]["multi_select"] == [{"name": "project-append"}]

    def test_project_not_found_falls_through(self, mock_gemini, mock_notion):
        """If project name doesn't match, falls back to normal task creation."""
        responses = [
            make_gemini_response({
                "category": "tasks",
                "related_project": "NonExistentProject",
            }),
            make_gemini_response({
                "Name": "Some task",
                "Tags": ["Chore"],
                "Due Date": "2026-03-29",
            }),
            make_gemini_response({"mobile_compatible": False}),
        ]
        mock_gemini.models.generate_content.side_effect = responses

        _run(_item("Some task", "NonExistentProject"))
        # Should still create via execute_logic fallback
        assert mock_notion.pages.create.called
        # Not a project-append execution — log must NOT carry the tag
        assert "Tags" not in _log_props(mock_notion)


# ======================================================================
# Grocery Tests
# ======================================================================
class TestGroceryPipeline:
    def test_new_grocery(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "groceries", {
            "Name": "Quinoa",
            "Category": "Grains",
            "Status": "On List",
        })

        _run(_item("Buy quinoa", "groceries"))
        assert mock_notion.pages.create.called

    def test_existing_grocery_update(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "groceries", {
            "Name": "Eggs",
            "Status": "On List",
        })

        _run(_item("Buy eggs", "groceries"))
        # Existing item → update status
        mock_notion.pages.update.assert_called()


# ======================================================================
# YouTube Tests
# ======================================================================
class TestYouTubePipeline:
    def test_new_video(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "youtube-videos", {
            "Title": "Great Video",
            "Video URL": "https://youtu.be/abc123",
            "Status": "Watched",
            "channel_handle": "@TestChannel",
        })
        mock_notion.request.return_value = {"results": []}

        with patch("core.handlers.get_video_channel_details", return_value=None):
            _run(_item("https://youtu.be/abc123"))
        assert mock_notion.pages.create.called


# ======================================================================
# Movie/TV Tests
# ======================================================================
class TestMovieTvPipeline:
    def test_new_movie(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "movies", {
            "Title": "Inception",
            "Genres": ["Sci-Fi"],
            "Status": "Not Started",
        })
        mock_notion.request.return_value = {"results": []}

        _run(_item("Inception"))
        mock_notion.pages.create.assert_called()

    def test_existing_movie_status_update(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "movies", {
            "Title": "Inception",
            "Status": "Finished",
        })
        existing = make_notion_page("movie-id", "Title", "Inception")
        # fetch_existing_page in handle_movies_tv_logic calls notion.request
        mock_notion.request.return_value = {"results": [existing]}

        _run(_item("Inception", "watched"))
        # Should update existing movie status, not create new
        mock_notion.pages.update.assert_called()


# ======================================================================
# Bookmark Tests
# ======================================================================
class TestBookmarkPipeline:
    def test_new_bookmark(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "bookmarks", {
            "Description": "A cool dev tool",
            "Title": "DevTool",
            "URL": "https://devtool.io",
            "Tags": [],
        })
        mock_notion.request.return_value = {"results": []}

        with patch("core.external_data.fetch_web_metadata", return_value="HTML Title: DevTool\nContent..."):
            _run(_item("https://devtool.io"))
        assert mock_notion.pages.create.called

    def test_github_bookmark_auto_tagged(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "bookmarks", {
            "Description": "A repo",
            "Title": "owner/repo",
            "URL": "https://github.com/owner/repo",
            "Tags": [],
        })
        mock_notion.request.return_value = {"results": []}

        with patch("core.external_data.fetch_web_metadata", return_value="HTML Title: Repo\nContent..."):
            _run(_item("https://github.com/owner/repo"))
        assert mock_notion.pages.create.called


# ======================================================================
# People Tests
# ======================================================================
class TestPeoplePipeline:
    def test_new_person(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "people", {
            "Name": "Arun Mehta",
            "Company": "Vantage Senior Associate",
        })

        _run(_item("Arun Vantage senior associate"))
        mock_notion.pages.create.assert_called()


# ======================================================================
# Quote Tests
# ======================================================================
class TestQuotePipeline:
    def test_new_quote(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "quotes", {
            "Quote": "I will be back",
        })

        _run(_item("I will be back", "Arnold"))
        assert mock_notion.pages.create.called


# ======================================================================
# Ideas Tests
# ======================================================================
class TestIdeaPipeline:
    def test_new_idea(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "ideas", {
            "Description": "App that tracks sleep patterns",
            "Tags": ["Tech"],
            "Status": "Ideated",
        })

        _run(_item("Idea for an app that tracks sleep patterns"))
        mock_notion.pages.create.assert_called()


# ======================================================================
# Fun Activities Tests
# ======================================================================
class TestFunActivitiesPipeline:
    def test_with_location(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "fun-activities", {
            "Title": "Walk around Seaport",
            "Status": "To Do",
            "Location": "Boston",
        })
        mock_notion.request.return_value = {"results": []}

        _run(_item("Walk around Seaport", "fun"))
        mock_notion.pages.create.assert_called()


# ======================================================================
# Podcast Tests
# ======================================================================
class TestPodcastPipeline:
    def test_spotify_podcast(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "podcasts", {
            "Episode Title": "Great Episode",
            "Podcast Name": "My Show",
            "Genres": ["Comedy"],
            "Status": "Not Started",
            "URL": "https://open.spotify.com/episode/abc",
        })

        with patch("core.external_data.get_spotify_metadata", return_value="Show: My Show\nEp: Great Episode\nDesc: Good"):
            _run(_item("https://open.spotify.com/episode/abc"))
        mock_notion.pages.create.assert_called()


# ======================================================================
# Bucket List Tests
# ======================================================================
class TestBucketListPipeline:
    def test_new_item(self, mock_gemini, mock_notion):
        _setup_classify_extract(mock_gemini, "bucket-list", {
            "Item": "Skydive in Dubai",
            "Tags": ["Adventure"],
        })

        _run(_item("Skydive in Dubai", "bucket list"))
        mock_notion.pages.create.assert_called()


# ======================================================================
# Error Handling Tests
# ======================================================================
class TestErrorHandling:
    def test_pipeline_error_logs_and_creates_task(self, mock_gemini, mock_notion):
        """When the pipeline throws, it should log the error and create a high-priority task."""
        mock_gemini.models.generate_content.side_effect = Exception("Gemini down")

        _run(_item("Some text that fails"))
        # Should have called create for: log_job_outcome + create_high_priority_task
        assert mock_notion.pages.create.call_count >= 1


# ======================================================================
# Batch Processing (processor entry point)
# ======================================================================
class TestProcessorEntryPoint:
    def test_batch_processing(self, mock_gemini, mock_notion):
        """run() should read raw_text from the payload, parse, and run pipeline for each item."""
        # Mock parse_raw_input to return 2 items
        with patch("core.pipeline.parse_raw_input") as mock_parse, \
             patch("core.pipeline.hydrate_dynamic_options"), \
             patch("core.pipeline.fetch_active_projects", return_value=(["Synapse"], {"Synapse": "id"})), \
             patch("core.pipeline.fetch_inventory_map", return_value={}), \
             patch("core.pipeline.fetch_trips_inventory", return_value=([], {})):

            mock_parse.return_value = [
                {"core_text": "Buy milk", "context_notes": "groceries"},
                {"core_text": "Call John", "context_notes": ""},
            ]

            # Mock the Gemini calls for each pipeline run (classify + extract per item)
            mock_gemini.models.generate_content.side_effect = [
                make_gemini_response({"category": "groceries"}),
                make_gemini_response({"Name": "Milk", "Status": "On List", "Category": "Dairy"}),
                make_gemini_response({"category": "tasks"}),
                make_gemini_response({"Name": "Call John", "Tags": ["Chore"], "Due Date": "2026-03-29"}),
                make_gemini_response({"mobile_compatible": False}),
            ]

            run({"raw_text": "Buy milk $ groceries @ Call John"})

            mock_parse.assert_called_once()
