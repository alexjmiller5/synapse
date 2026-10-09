"""End-to-end pipeline tests: realistic captures through run_pipeline inside a
real capture journal, landing as rows on a synthetic Soma hub."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from core.pipeline import run, run_pipeline
from core.schemas import CATEGORY_SCHEMA_CLASSIFY
from core.workflow import capture_scope
from helpers import make_gemini_response


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
    mock_gemini.models.generate_content.side_effect = [
        make_gemini_response(classify_resp),
        make_gemini_response(extracted),
    ]


DEFAULT_CTX = {
    "project_prompts": ["Synapse"],
    "project_id_map": {"Synapse": "synapse-project-id"},
    "inventory_map": {"Eggs": "eggs-id", "Milk": "milk-id"},
    "inventory_list": ["Eggs", "Milk"],
}


@pytest.fixture
def hub(media_hub):
    return media_hub


def _run(item_data, **overrides):
    """One parsed item, processed inside its own capture journal."""
    ctx = {**DEFAULT_CTX, **overrides}
    payload = {
        "raw_text": item_data["core_text"],
        "workspace": "default",
        "capture_id": str(uuid4()),
    }
    with capture_scope({}, payload):
        run_pipeline(
            item_data,
            ctx["project_prompts"],
            ctx["project_id_map"],
            ctx["inventory_map"],
            ctx["inventory_list"],
            source=ctx.get("source"),
        )


def _rows(hub, table):
    return list(hub.rows.get(table, {}).values())


def _execution(hub):
    (execution,) = _rows(hub, "synapse_executions")
    return execution


def _task(hub):
    (task,) = _rows(hub, "tasks")
    return task


# ======================================================================
# Task Tests
# ======================================================================
class TestTaskPipeline:
    def test_simple_task(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "tasks",
            {"Name": "Update dating profile", "Tags": ["Chore"], "Due Date": "2026-03-29"},
        )
        _run(_item("Update dating profile"))
        task = _task(hub)
        assert task["title"] == "Update dating profile"
        assert task["tags"] == ["Chore"] and task["due_date"] == "2026-03-29"
        assert task["status"] == "To Do" and task["priority"] == "High"
        execution = _execution(hub)
        assert execution["created_item"] == f"tasks/{task['id']}"
        assert execution["code_execution"] == "Success" and execution["category"] == "tasks"
        assert execution["outcome"] == "To Review"
        assert "tags" not in execution  # ordinary execution: not a project append

    def test_task_name_is_the_verbatim_capture(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "tasks",
            {
                "Name": "Cancel the Uber One subscription",
                "Tags": ["Chore"],
                "Due Date": "2027-01-01",
            },
        )
        _run(_item("Cancel Uber One", "Jan 1"))
        task = _task(hub)
        assert task["title"] == "Cancel Uber One" and task["due_date"] == "2027-01-01"


# ======================================================================
# Deterministic task-context pre-check
# ======================================================================
class TestTaskContextPrecheck:
    def test_task_context_skips_classifier(self, hub, mock_gemini):
        """Context containing the word 'task' classifies deterministically — no classify call."""
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response(
                {
                    "Name": "Add the full x men series to my movies db",
                    "Tags": ["Chore"],
                    "Due Date": "2026-07-10",
                }
            ),
        ]
        _run(_item("Add the full x men series to my movies db", "med prior task"))
        assert mock_gemini.models.generate_content.call_count == 1
        first_cfg = mock_gemini.models.generate_content.call_args_list[0].kwargs["config"]
        assert first_cfg.response_json_schema is not CATEGORY_SCHEMA_CLASSIFY
        assert _task(hub)["title"] == "Add the full x men series to my movies db"

    def test_date_context_still_calls_classifier(self, hub, mock_gemini):
        """A plain date context does NOT trigger the pre-check — classifier runs."""
        _setup_classify_extract(
            mock_gemini,
            "tasks",
            {
                "Name": "watch Eric Andre's new movie, little brother",
                "Tags": ["Chore"],
                "Due Date": "2026-06-26",
            },
        )
        _run(_item("watch Eric Andre's new movie, little brother", "June 26"))
        assert mock_gemini.models.generate_content.call_count == 2
        first_cfg = mock_gemini.models.generate_content.call_args_list[0].kwargs["config"]
        assert first_cfg.response_json_schema is CATEGORY_SCHEMA_CLASSIFY
        assert _task(hub)["due_date"] == "2026-06-26"


# ======================================================================
# Project Tasks
# ======================================================================
class TestProjectPipeline:
    def test_project_task(self, hub, mock_gemini):
        """Task with project context creates a project-linked task."""
        _setup_classify_extract(
            mock_gemini,
            "tasks",
            {"Name": "Fix login bug", "Tags": ["Chore"], "Due Date": "2026-03-29"},
            project="Synapse",
        )
        _run(_item("Fix login bug", "Synapse"))
        task = _task(hub)
        assert task["project_ids"] == ["synapse-project-id"]
        assert task["priority"] == "High"
        assert _execution(hub)["tags"] == ["project-append"]

    def test_task_context_links_project(self, hub, mock_gemini):
        """The deterministic 'task' pre-check still links a referenced project."""
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response(
                {"Name": "Fix Synapse login bug", "Tags": ["Chore"], "Due Date": "2026-07-10"}
            ),
        ]
        _run(_item("Fix Synapse login bug", "high priority task"))
        assert mock_gemini.models.generate_content.call_count == 1
        assert _task(hub)["project_ids"] == ["synapse-project-id"]
        assert _execution(hub)["tags"] == ["project-append"]

    def test_task_context_typod_project_rescued_by_classifier(self, hub, mock_gemini):
        """When the contains-match misses but the text mentions a project ('proj'),
        one classifier call rescues the link — category stays tasks."""
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({"category": "tasks", "related_project": "Task Burndown Chart"}),
            make_gemini_response(
                {"Name": "add manual markers on dates", "Tags": ["Chore"], "Due Date": "2026-08-03"}
            ),
        ]
        _run(
            _item("add manual markers on dates", "task burdown chart proj"),
            project_prompts=["Task Burndown Chart"],
            project_id_map={"Task Burndown Chart": "burndown-id"},
        )
        assert mock_gemini.models.generate_content.call_count == 2
        assert _task(hub)["project_ids"] == ["burndown-id"]

    def test_task_context_without_proj_mention_skips_rescue(self, hub, mock_gemini):
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response(
                {"Name": "clean the desk", "Tags": ["Chore"], "Due Date": "2026-08-03"}
            ),
        ]
        _run(_item("clean the desk", "low prior task"))
        assert mock_gemini.models.generate_content.call_count == 1

    def test_project_not_found_falls_through(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "tasks",
            {"Name": "Some task", "Tags": ["Chore"], "Due Date": "2026-03-29"},
            project="NonExistentProject",
        )
        _run(_item("Some task", "NonExistentProject"))
        assert "project_ids" not in _task(hub)
        assert "tags" not in _execution(hub)


# ======================================================================
# Groceries
# ======================================================================
class TestGroceryPipeline:
    def test_new_grocery_is_pushed_to_the_hub(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini, "groceries", {"Name": "Quinoa", "Category": "Grains", "Status": "On List"}
        )
        _run(_item("Buy quinoa", "groceries"))
        (row,) = _rows(hub, "groceries")
        assert (row["name"], row["category"], row["status"]) == ("Quinoa", "Grains", "On List")
        assert _execution(hub)["created_item"] == f"groceries/{row['id']}"
        assert _rows(hub, "tasks") == []

    def test_existing_grocery_updates_that_row(self, hub, mock_gemini):
        hub.rows["groceries"] = {"egg-id": {"id": "egg-id", "name": "Eggs", "status": "Have"}}
        _setup_classify_extract(mock_gemini, "groceries", {"Name": "Eggs", "Status": "On List"})
        _run(_item("Buy eggs", "groceries"))
        (row,) = _rows(hub, "groceries")
        assert row["id"] == "egg-id" and row["status"] == "On List"


# ======================================================================
# YouTube
# ======================================================================
class TestYouTubePipeline:
    SNIPPET = {
        "items": [
            {
                "id": "abc123",
                "snippet": {
                    "title": "Great Video",
                    "channelId": "UCabc",
                    "channelTitle": "Test Channel",
                    "publishedAt": "2020-01-01T00:00:00Z",
                },
                "contentDetails": {"duration": "PT10M"},
            }
        ]
    }
    CHANNEL = {
        "items": [
            {
                "id": "UCabc",
                "snippet": {"title": "Test Channel", "customUrl": "@testchannel"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUabc"}},
            }
        ]
    }

    def _yt(self):
        yt = MagicMock()
        yt.videos().list().execute.return_value = self.SNIPPET
        yt.channels().list().execute.return_value = self.CHANNEL
        return yt

    def test_new_video_pushed_to_soma(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "youtube-videos",
            {"Video URL": "https://youtu.be/abc123", "Status": "Finished"},
        )
        with patch("core.handlers.get_youtube", return_value=self._yt()):
            _run(_item("https://youtu.be/abc123"))
        assert [r["id"] for r in _rows(hub, "youtube_channels")] == ["UCabc"]
        (video,) = _rows(hub, "youtube_videos")
        assert video["id"] == "abc123" and video["status"] == "Finished"
        execution = _execution(hub)
        assert execution["created_item"] == "youtube_videos/abc123"
        assert execution["category"] == "youtube-videos"
        # the new channel asks the user to classify it
        assert [t["title"] for t in _rows(hub, "tasks")] == ["Classify new Channel: Test Channel"]

    def test_youtube_homepage_url_fails_with_a_review_task(self, hub, mock_gemini):
        """A videoless YouTube URL writes no video; the execution is an error and
        a cleanup task asks the user to file it (never a silent retry loop)."""
        _setup_classify_extract(
            mock_gemini,
            "youtube-videos",
            {"Video URL": "https://youtube.com/", "Status": "Not Started"},
        )
        _run(_item("https://youtube.com/"))
        assert _rows(hub, "youtube_videos") == []
        assert _execution(hub)["code_execution"] == "Error(s)"
        assert "youtube.com" in _task(hub)["title"]


# ======================================================================
# Movies / TV
# ======================================================================
class TestMovieTvPipeline:
    def test_movie_pushed_to_soma_and_logged_by_row_ref(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "movies",
            {"Title": "Inception", "Status": "Not Started", "Tags": ["Favorite"]},
        )
        with patch("core.handlers.resolve_tmdb_id", return_value="27205"):
            _run(_item("Inception"))
        (movie,) = _rows(hub, "movies")
        assert movie["id"] == "27205" and movie["tags"] == ["Favorite"]
        execution = _execution(hub)
        assert execution["created_item"] == "movies/27205"
        assert execution["category"] == "movies"

    def test_unresolvable_movie_files_a_cleanup_task(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini, "movies", {"Title": "Some Obscure Film", "Status": "Priority"}
        )
        with patch("core.handlers.resolve_tmdb_id", return_value=None):
            _run(_item("Some Obscure Film"))
        assert _rows(hub, "movies") == []
        execution = _execution(hub)
        assert execution["code_execution"] == "Error(s)"
        assert execution.get("created_item") is None
        assert "TMDB" in _task(hub)["title"]


# ======================================================================
# Bookmarks
# ======================================================================
class TestBookmarkPipeline:
    def test_new_bookmark(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "bookmarks",
            {
                "Description": "A cool dev tool",
                "Title": "DevTool",
                "URL": "https://devtool.io",
                "Tags": [],
            },
        )
        with patch(
            "core.external_data.fetch_web_metadata", return_value="HTML Title: DevTool\nContent..."
        ):
            _run(_item("https://devtool.io"))
        (row,) = _rows(hub, "bookmarks")
        assert row["url"] == "https://devtool.io" and row["title"] == "DevTool"

    def test_github_bookmark_auto_tagged(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "bookmarks",
            {
                "Description": "A repo.",
                "Title": "owner/repo",
                "URL": "https://github.com/owner/repo",
                "Tags": [],
            },
        )
        with patch(
            "core.external_data.fetch_web_metadata", return_value="HTML Title: Repo\nContent..."
        ):
            _run(_item("https://github.com/owner/repo"))
        (row,) = _rows(hub, "bookmarks")
        assert row["tags"] == ["Github"] and row["description"] == "A repo"

    def test_failed_scrape_flags_the_row_instead_of_filing_a_task(self, hub, mock_gemini):
        # A login-walled or bot-checked page: the model can only guess from the URL.
        _setup_classify_extract(
            mock_gemini,
            "bookmarks",
            {
                "Description": "Instagram direct messages",
                "Title": "Instagram",
                "URL": "https://www.instagram.com/direct/inbox/",
                "Tags": [],
            },
        )
        with patch("core.external_data.fetch_web_metadata", return_value="Error fetching metadata"):
            _run(_item("https://www.instagram.com/direct/inbox/"))
        (row,) = _rows(hub, "bookmarks")
        assert "title" not in row  # a guessed title would break "the page's own title"
        assert row["needs_review"] and row["description"] == "Instagram direct messages"
        assert _rows(hub, "tasks") == []

    def test_failed_scrape_of_a_known_bookmark_changes_nothing(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "bookmarks",
            {"Description": "A guess", "Title": "Guess", "URL": "https://x.com", "Tags": ["Money"]},
        )
        known = {
            "id": "bm1",
            "url": "https://x.com",
            "title": "X",
            "description": "Real",
            "tags": '["List"]',
            "needs_review": None,
        }
        hub.rows["bookmarks"] = {"bm1": dict(known)}
        with patch("core.external_data.fetch_web_metadata", return_value="Error fetching metadata"):
            _run(_item("https://x.com"))
        (row,) = _rows(hub, "bookmarks")
        assert {k: row[k] for k in known} == known


# ======================================================================
# Ideas, things to do, podcasts
# ======================================================================
class TestCategoryRows:
    def test_new_idea(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "ideas",
            {
                "Description": "App that tracks sleep patterns",
                "Tags": ["Coding"],
                "Status": "Someday",
            },
        )
        _run(_item("Idea for an app that tracks sleep patterns"))
        (row,) = _rows(hub, "ideas")
        assert row["status"] == "Someday" and row["tags"] == ["Coding"]

    def test_fun_activity_with_location(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "fun-activities",
            {"Title": "Walk around Seaport", "Status": "Someday", "Location": "Boston"},
        )
        _run(_item("Walk around Seaport", "fun"))
        (row,) = _rows(hub, "things_to_do")
        assert row["kind"] == "Activity" and row["city"] == "Boston"
        assert "needs_review" not in row

    def test_bucket_list_item_is_an_ambition(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini, "bucket-list", {"Item": "Skydive in Dubai", "Tags": ["Adventure"]}
        )
        _run(_item("Skydive in Dubai", "bucket list"))
        (row,) = _rows(hub, "things_to_do")
        assert row["kind"] == "Ambition" and row["title"] == "Skydive in Dubai"

    def test_spotify_podcast(self, hub, mock_gemini):
        _setup_classify_extract(
            mock_gemini,
            "podcasts",
            {
                "Episode Title": "Great Episode",
                "Podcast Name": "My Show",
                "Genres": ["Comedy"],
                "Status": "Not Started",
                "URL": "https://open.spotify.com/episode/abc",
            },
        )
        with patch(
            "core.external_data.get_spotify_metadata",
            return_value="Show: My Show\nEp: Great Episode\nDesc: Good",
        ):
            _run(_item("https://open.spotify.com/episode/abc"))
        (row,) = _rows(hub, "podcast_episodes")
        assert row["podcast"] == "My Show" and row["url"].startswith("https://open.spotify.com")


# ======================================================================
# Errors
# ======================================================================
class TestErrorHandling:
    def test_a_failed_preparation_files_a_recovery_task_and_logs_the_error(self, hub, mock_gemini):
        mock_gemini.models.generate_content.side_effect = Exception("Gemini down")
        _run(_item("Some text that fails"))
        task = _task(hub)
        assert task["title"].endswith("Some text that fails") and task["priority"] == "High"
        execution = _execution(hub)
        assert execution["code_execution"] == "Error(s)" and execution["category"] == "Unknown"
        assert "Gemini down" in execution["error_details"]
        assert execution["created_item"] == f"tasks/{task['id']}"


# ======================================================================
# The worker entry point
# ======================================================================
class TestProcessorEntryPoint:
    PAYLOAD = {
        "raw_text": "Buy milk $ groceries @ Call John",
        "workspace": "default",
        "capture_id": "0d4bbad3-41a2-4f40-9ba6-0c1d13c3a7a1",
    }

    def _gemini(self, mock_gemini):
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({"category": "groceries"}),
            make_gemini_response({"Name": "Milk", "Status": "On List", "Category": "Dairy"}),
            make_gemini_response({"category": "tasks"}),
            make_gemini_response(
                {"Name": "Call John", "Tags": ["Chore"], "Due Date": "2026-03-29"}
            ),
        ]

    def test_batch_processing_lands_every_item_and_its_execution(self, hub, mock_gemini):
        hub.rows["projects"] = {"p1": {"id": "p1", "title": "Synapse", "status": "To Do"}}
        self._gemini(mock_gemini)
        with patch(
            "core.pipeline.parse_raw_input",
            return_value=[
                {"core_text": "Buy milk", "context_notes": "groceries"},
                {"core_text": "Call John", "context_notes": ""},
            ],
        ) as parse:
            run(dict(self.PAYLOAD), store={})
        parse.assert_called_once()
        assert [r["name"] for r in _rows(hub, "groceries")] == ["Milk"]
        assert [r["title"] for r in _rows(hub, "tasks")] == ["Call John"]
        assert len(_rows(hub, "synapse_executions")) == 2

    def test_a_resent_capture_id_writes_nothing_new(self, hub, mock_gemini):
        self._gemini(mock_gemini)
        store = {}
        items = [
            {"core_text": "Buy milk", "context_notes": "groceries"},
            {"core_text": "Call John", "context_notes": ""},
        ]
        with patch("core.pipeline.parse_raw_input", return_value=items):
            run(dict(self.PAYLOAD), store=store)
            run(dict(self.PAYLOAD), store=store)
        assert mock_gemini.models.generate_content.call_count == 4
        assert len(_rows(hub, "synapse_executions")) == 2

    def test_a_workspace_without_soma_task_bindings_is_refused(self, monkeypatch):
        from core import workflow

        monkeypatch.setattr(
            workflow, "binding_for", lambda kind: None if kind == "tasks" else {"table": "x"}
        )
        monkeypatch.setattr("core.pipeline.binding_for", workflow.binding_for)
        with pytest.raises(ValueError, match="workflow.tasks"):
            run(dict(self.PAYLOAD), store={})

    def test_a_capture_without_its_id_is_refused(self, hub):
        payload = {k: v for k, v in self.PAYLOAD.items() if k != "capture_id"}
        with pytest.raises(ValueError, match="capture_id"):
            run(payload, store={})


# ======================================================================
# Source stamp + `pj` keyword
# ======================================================================
class TestSourceAndPjKeyword:
    def _task_extraction(self, mock_gemini, name):
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({"Name": name, "Tags": ["Chore"], "Due Date": "2026-08-03"}),
        ]

    def test_source_is_logged_on_the_execution(self, hub, mock_gemini):
        self._task_extraction(mock_gemini, "clean the desk")
        _run(_item("clean the desk", "task"), source="ios-app")
        assert _execution(hub)["source"] == "ios-app"

    def test_missing_source_leaves_the_column_unset(self, hub, mock_gemini):
        self._task_extraction(mock_gemini, "clean the desk")
        _run(_item("clean the desk", "task"))
        assert "source" not in _execution(hub)

    def test_pj_forces_a_project_task_and_is_stripped(self, hub, mock_gemini):
        """`pj` anywhere in the capture = project task: no classifier call, the
        project comes from the contains-match, and the keyword never reaches the
        task name."""
        self._task_extraction(mock_gemini, "fix the url bug synapse")
        _run(_item("fix the url bug pj synapse"))
        assert mock_gemini.models.generate_content.call_count == 1
        task = _task(hub)
        assert task["project_ids"] == ["synapse-project-id"]
        assert task["title"] == "fix the url bug synapse"
        assert _execution(hub)["tags"] == ["project-append"]

    def test_pj_in_context_without_name_match_uses_classifier_rescue(self, hub, mock_gemini):
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({"category": "tasks", "related_project": "Synapse"}),
            make_gemini_response(
                {"Name": "fix the url bug", "Tags": ["Chore"], "Due Date": "2026-08-03"}
            ),
        ]
        _run(_item("fix the url bug", "pj the thought app"))
        assert mock_gemini.models.generate_content.call_count == 2
        assert _task(hub)["project_ids"] == ["synapse-project-id"]

    def test_pj_inside_a_word_is_not_the_keyword(self, hub, mock_gemini):
        mock_gemini.models.generate_content.side_effect = [
            make_gemini_response({"category": "groceries"}),
            make_gemini_response({"Name": "Pajamas", "Category": "Snacks", "Status": "On List"}),
        ]
        _run(_item("buy new pjs"))
        assert mock_gemini.models.generate_content.call_count == 2
