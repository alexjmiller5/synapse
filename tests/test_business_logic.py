"""Tests for business_logic.py — business rules, inventory, projects."""

from unittest.mock import patch

from core.timeutils import today_eastern

from core.business_logic import (
    apply_business_logic,
    clean_text,
    execute_logic,
    fetch_inventory_map,
    fetch_active_projects,
)


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

    def test_movies_omitted_status_stays_unspecified(self):
        data = {"Title": "Inception"}
        result = apply_business_logic("movies", data)
        assert "Status" not in result

    def test_movies_keeps_explicit_status(self):
        data = {"Title": "Inception", "Status": "Finished"}
        result = apply_business_logic("movies", data)
        assert result["Status"] == "Finished"

    def test_tv_show_omitted_status_stays_unspecified(self):
        assert "Status" not in apply_business_logic("tv-shows", {"Title": "Severance"})

    def test_tasks_empty_due_date_dropped(self):
        """Place-tagged tasks are dateless: the prompt returns '' for Due Date and
        the empty value must be removed, never sent to Notion."""
        result = apply_business_logic(
            "tasks", {"Name": "fix the dock lines", "Tags": ["Lake House"], "Due Date": ""}
        )
        assert "Due Date" not in result

    def test_podcasts_finished_sets_date(self):
        data = {"Episode Title": "Ep1", "Status": "Finished"}
        result = apply_business_logic("podcasts", data)
        assert result["Date Listened To"] == today_eastern().isoformat()

    def test_podcasts_not_finished_no_date(self):
        data = {"Episode Title": "Ep1", "Status": "Not Started"}
        result = apply_business_logic("podcasts", data)
        assert "Date Listened To" not in result

    def test_youtube_status_never_gets_a_date(self):
        data = {"Title": "Video", "Status": "Finished"}
        assert "Date Watched" not in apply_business_logic("youtube-videos", data)

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
        result = apply_business_logic("quotes", data)
        assert result == data

    def test_people_route_retired(self):
        """People live in soma; Synapse never writes them."""
        from core.business_logic import LOGIC_HANDLERS

        assert "people" not in LOGIC_HANDLERS


# ======================================================================
# fetch_inventory_map
# ======================================================================
class TestFetchInventoryMap:
    def test_builds_map_from_hub_rows(self):
        rows = [{"id": "id-1", "name": "Eggs"}, {"id": "id-2", "name": "Milk"}]
        with patch("core.business_logic.pull_rows", return_value=rows) as pull:
            inventory = fetch_inventory_map("groceries")
        assert inventory == {"Eggs": "id-1", "Milk": "id-2"}
        assert pull.call_args.args == ("groceries", ["name"])

    def test_hub_unavailable_is_an_empty_inventory(self):
        with patch("core.business_logic.pull_rows", side_effect=RuntimeError("no hub")):
            assert fetch_inventory_map("groceries") == {}


# ======================================================================
# fetch_active_projects
# ======================================================================
class TestFetchActiveProjects:
    def test_reads_the_workflow_projects_table(self):
        rows = [
            {"id": "p1", "title": "Synapse", "status": "In progress"},
            {"id": "p2", "title": "Blueprint", "status": "To Do"},
            {"id": "p3", "title": "Old", "status": "Completed"},
        ]
        with patch("core.soma_hub.pull_rows", return_value=rows) as pull:
            prompt_list, id_map = fetch_active_projects()
        assert prompt_list == ["Synapse", "Blueprint"]
        assert id_map == {"Synapse": "p1", "Blueprint": "p2"}
        assert pull.call_args.args == ("projects", ["title", "status"])

    def test_no_projects_binding_means_no_projects(self, monkeypatch):
        from core import workflow

        monkeypatch.setattr(workflow, "binding_for", lambda kind: None)
        assert fetch_active_projects() == ([], {})


# ======================================================================
# execute_logic
# ======================================================================
class TestExecuteLogic:
    def test_tasks_are_soma_task_rows(self):
        data = {"Name": "Test task", "Status": "To Do", "Tags": ["Chore"]}
        with patch("core.workflow.create_task", return_value="tasks/abc") as create:
            assert execute_logic("tasks", data) == "tasks/abc"
        create.assert_called_once_with(data)

    def test_hub_backed_category_routes_to_the_hub_handler(self):
        data = {"Name": "New Item", "Status": "On List"}
        with patch("core.business_logic.handle_hub_logic", return_value="groceries/x") as hub:
            assert execute_logic("groceries", data, inventory_map={}) == "groceries/x"
        hub.assert_called_once_with("groceries", data)

    def test_resolved_id_categories_use_their_handler(self):
        with patch.dict(
            "core.business_logic.LOGIC_HANDLERS", {"movies": lambda c, d: "movies/603"}
        ):
            assert execute_logic("movies", {"Title": "The Matrix"}) == "movies/603"


def test_project_routing_preserves_extracted_task_notes():
    result = apply_business_logic(
        "tasks",
        {"Name": "Follow up", "Notes": "Keep the supplied details"},
        related_project="Example Project",
    )
    assert result["Notes"] == "Keep the supplied details\n\nProject: Example Project"


# ======================================================================
# clean_text — deterministic de-spam / de-mojibake of a task's verbatim name
# ======================================================================
class TestCleanText:
    def test_empty_and_none_safe(self):
        assert clean_text("") == ""
        assert clean_text(None) is None  # non-str passes through untouched

    def test_clean_string_is_noop(self):
        assert clean_text("Buy milk") == "Buy milk"

    def test_strips_leading_trailing_whitespace(self):
        assert clean_text("  hello  \n") == "hello"

    def test_collapses_newline_spam(self):
        assert clean_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_keeps_double_newline(self):
        assert clean_text("a\n\nb") == "a\n\nb"

    def test_collapses_punctuation_spam(self):
        assert clean_text("wait———really") == "wait—really"
        assert clean_text("well....") == "well…"
        assert clean_text("stop!!!!") == "stop!"
        assert clean_text("what???") == "what?"

    def test_short_repeats_untouched(self):
        assert clean_text("a—b") == "a—b"
        assert clean_text("wait..") == "wait.."
        assert clean_text("yes!!") == "yes!!"

    def test_mojibake_quotes_dashes_and_spaces(self):
        rsquo = "\u00e2\u20ac\u2122"
        assert clean_text("It" + rsquo + "s here") == "It's here"
        assert clean_text("a" + "\u00e2\u20ac\u201d" + "b") == "a\u2014b"
        assert clean_text("a\u00c2\u00a0b") == "a b"
        assert clean_text("a\u00a0b") == "a b"
        assert clean_text("\ufeffhello") == "hello"

    def test_preserves_accents(self):
        assert clean_text("Sérgio") == "Sérgio"
        assert clean_text("l'âme") == "l'âme"

    def test_idempotent(self):
        messy = "Buy milk!!!!\n\n\n\nnow...."
        once = clean_text(messy)
        assert clean_text(once) == once
