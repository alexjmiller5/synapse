"""Tests for notion_utils.py — property builders, CRUD operations, logging."""

from unittest.mock import patch
import pytest

from core.notion_utils import (
    _notion_title,
    _notion_rich_text,
    _notion_multi_select,
    _notion_date,
    _notion_status,
    _notion_select,
    _notion_url,
    clean_text,
    build_notion_properties,
    create_page,
    update_status,
    log_job_outcome,
    create_cleanup_task,
    create_high_priority_task,
    create_project_task,
    fetch_existing_page,
)
from helpers import make_notion_page


# ======================================================================
# clean_text — deterministic de-spam / de-mojibake
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

    def test_collapses_em_dash_spam(self):
        assert clean_text("wait———really") == "wait—really"

    def test_single_em_dash_untouched(self):
        assert clean_text("a—b") == "a—b"

    def test_collapses_dot_spam_to_ellipsis(self):
        assert clean_text("well....") == "well…"
        assert clean_text("hmm.......") == "hmm…"

    def test_two_dots_untouched(self):
        # Conservative: only 3+ collapse; a legit ".." is left alone.
        assert clean_text("wait..") == "wait.."

    def test_collapses_bang_spam(self):
        assert clean_text("stop!!!!") == "stop!"

    def test_collapses_question_spam(self):
        assert clean_text("what???") == "what?"

    def test_double_bang_untouched(self):
        assert clean_text("yes!!") == "yes!!"

    def test_mojibake_smart_quotes(self):
        rsquo = "\u00e2\u20ac\u2122"  # a-euro-tm mojibake of U+2019
        ldquo = "\u00e2\u20ac\u0153"  # left double quote mojibake
        rdquo = "\u00e2\u20ac\u009d"  # right double quote mojibake
        assert clean_text("It" + rsquo + "s here") == "It's here"
        assert clean_text(ldquo + "quoted" + rdquo) == chr(34) + "quoted" + chr(34)

    def test_mojibake_em_dash(self):
        emdash = "\u00e2\u20ac\u201d"  # em-dash mojibake (CP1252 0x94)
        assert clean_text("a" + emdash + "b") == "a\u2014b"

    def test_mojibake_nbsp_variants(self):
        assert clean_text("a\u00c2\u00a0b") == "a b"  # mojibake of a NBSP
        assert clean_text("a\u00a0b") == "a b"  # bare NBSP
        assert clean_text("\ufeffhello") == "hello"  # BOM stripped

    def test_preserves_accents(self):
        # Alex has names with accents — never ASCII-fold them.
        assert clean_text("Sérgio") == "Sérgio"
        assert clean_text("Mémoire") == "Mémoire"
        # A legit standalone 'â' (French "âme") must survive — only â€… sequences go.
        assert clean_text("l'âme") == "l'âme"

    def test_idempotent(self):
        messy = "  Hey———there!!!!\n\n\n\ndone....  "
        once = clean_text(messy)
        assert clean_text(once) == once

    def test_title_applies_clean_text(self):
        content = _notion_title("Buy milk!!!!\n\n\n\n")["title"][0]["text"]["content"]
        assert content == "Buy milk!"

    def test_rich_text_applies_clean_text(self):
        content = _notion_rich_text("done....")["rich_text"][0]["text"]["content"]
        assert content == "done…"

    def test_rich_text_whitespace_only_becomes_empty(self):
        # clean_text strips to "" → treated as empty rich_text.
        assert _notion_rich_text("   \n\n  ") == {"rich_text": []}


# ======================================================================
# Property builder helpers
# ======================================================================
class TestPropertyBuilders:
    def test_notion_title(self):
        result = _notion_title("My Title")
        assert result == {"title": [{"text": {"content": "My Title"}}]}

    def test_notion_rich_text(self):
        result = _notion_rich_text("Some text")
        assert result == {"rich_text": [{"text": {"content": "Some text"}}]}

    def test_notion_rich_text_empty(self):
        result = _notion_rich_text("")
        assert result == {"rich_text": []}

    def test_notion_rich_text_none(self):
        result = _notion_rich_text(None)
        assert result == {"rich_text": []}

    def test_notion_multi_select(self):
        result = _notion_multi_select(["Tag1", "Tag2"])
        assert result == {"multi_select": [{"name": "Tag1"}, {"name": "Tag2"}]}

    def test_notion_multi_select_empty(self):
        result = _notion_multi_select([])
        assert result == {"multi_select": []}

    def test_notion_multi_select_strips_commas(self):
        # Notion rejects option names containing commas (e.g. "Tyler, the Creator")
        result = _notion_multi_select(["Tyler, the Creator", "Anne Hathaway"])
        assert result == {"multi_select": [{"name": "Tyler the Creator"}, {"name": "Anne Hathaway"}]}

    def test_notion_date(self):
        result = _notion_date("2026-01-15")
        assert result == {"date": {"start": "2026-01-15"}}

    def test_notion_date_accepts_iso_datetime(self):
        result = _notion_date("2026-01-15T09:30:00")
        assert result == {"date": {"start": "2026-01-15T09:30:00"}}

    def test_notion_date_empty_raises(self):
        """An empty date must raise (tracked as a bug) rather than reach Notion."""
        with pytest.raises(ValueError):
            _notion_date("")

    def test_notion_date_natural_language_raises(self):
        """Non-ISO strings the AI sometimes emits must raise, not 500 silently at Notion."""
        with pytest.raises(ValueError):
            _notion_date("Sep 1")
        with pytest.raises(ValueError):
            _notion_date("After graduation")

    def test_notion_status(self):
        result = _notion_status("To Do")
        assert result == {"status": {"name": "To Do"}}

    def test_notion_select(self):
        result = _notion_select("High")
        assert result == {"select": {"name": "High"}}

    def test_notion_select_empty(self):
        assert _notion_select("") is None
        assert _notion_select(None) is None

    def test_notion_url(self):
        result = _notion_url("https://example.com")
        assert result == {"url": "https://example.com"}

    def test_notion_title_truncates_over_2000(self):
        """Notion 400s on title content >2000 chars — truncate with an ellipsis."""
        content = _notion_title("x" * 5000)["title"][0]["text"]["content"]
        assert len(content) == 2000
        assert content.endswith("…")

    def test_notion_rich_text_truncates_over_2000(self):
        content = _notion_rich_text("y" * 5000)["rich_text"][0]["text"]["content"]
        assert len(content) == 2000
        assert content.endswith("…")

    def test_notion_title_under_limit_untouched(self):
        content = _notion_title("short")["title"][0]["text"]["content"]
        assert content == "short"


# ======================================================================
# build_notion_properties
# ======================================================================
class TestBuildNotionProperties:
    def test_tasks_basic(self):
        data = {
            "Name": "Update profile",
            "Tags": ["Chore"],
            "Status": "To Do",
            "Due Date": "2026-01-15",
        }
        props = build_notion_properties("tasks", data)
        assert props["Name"] == {"title": [{"text": {"content": "Update profile"}}]}
        assert props["Tags"] == {"multi_select": [{"name": "Chore"}]}
        assert props["Status"] == {"status": {"name": "To Do"}}
        assert props["Due Date"] == {"date": {"start": "2026-01-15"}}

    def test_tasks_ai_title_never_written(self):
        """The deleted 'AI Title' property must never reach Notion, even if an AI
        extraction still emits it — build_notion_properties drops unknown keys."""
        props = build_notion_properties("tasks", {"Name": "Do it", "AI Title": "Do it nicely"})
        assert "AI Title" not in props

    def test_tasks_with_links(self):
        data = {
            "Name": "Check site",
            "Links": ["https://a.com", "https://b.com"],
        }
        props = build_notion_properties("tasks", data)
        assert "https://a.com" in props["Links"]["rich_text"][0]["text"]["content"]

    def test_unknown_category_fallback(self):
        props = build_notion_properties("nonexistent", {"Name": "Test"})
        assert "Name" in props
        assert props["Name"]["title"][0]["text"]["content"] == "Test"

    def test_none_values_skipped(self):
        data = {"Name": "Test", "Priority": None}
        props = build_notion_properties("tasks", data)
        assert "Priority" not in props

    def test_date_field_valid_iso_ok(self):
        props = build_notion_properties("trips", {"Name": "Miami", "Dates": "2026-09-01"})
        assert props["Dates"] == {"date": {"start": "2026-09-01"}}

    def test_date_field_empty_raises(self):
        """Regression: page 35e0… — AI emitted Dates='' and the job 500'd at Notion."""
        with pytest.raises(ValueError):
            build_notion_properties("trips", {"Name": "Miami", "Dates": ""})

    def test_date_field_natural_language_raises(self):
        """Regression: pages 3650…/36d0… — AI emitted 'Sep 1' / 'After graduation'."""
        with pytest.raises(ValueError):
            build_notion_properties("trips", {"Name": "Ecuador", "Dates": "Sep 1"})
        with pytest.raises(ValueError):
            build_notion_properties("trips", {"Name": "Japan", "Dates": "After graduation"})

    def test_unknown_keys_skipped(self):
        data = {"Name": "Test", "FakeField": "value"}
        props = build_notion_properties("tasks", data)
        assert "FakeField" not in props

    def test_groceries(self):
        data = {"Name": "Eggs", "Category": "Dairy", "Status": "On List"}
        props = build_notion_properties("groceries", data)
        assert props["Name"]["title"][0]["text"]["content"] == "Eggs"
        assert props["Category"]["select"]["name"] == "Dairy"

    def test_bookmarks(self):
        data = {
            "Description": "A cool site",
            "Title": "Cool Site",
            "URL": "https://cool.com",
            "Tags": ["Github"],
        }
        props = build_notion_properties("bookmarks", data)
        assert props["URL"] == {"url": "https://cool.com"}
        assert props["Tags"] == {"multi_select": [{"name": "Github"}]}

    def test_checkbox_true(self):
        props = build_notion_properties("tasks", {"Name": "Refactor", "AI Ready": True})
        assert props["AI Ready"] == {"checkbox": True}

    def test_checkbox_false(self):
        props = build_notion_properties("tasks", {"Name": "Refactor", "AI Ready": False})
        assert props["AI Ready"] == {"checkbox": False}

    def test_checkbox_absent_omitted(self):
        """No 'AI Ready' key → not written; Notion defaults the checkbox to false."""
        props = build_notion_properties("tasks", {"Name": "Refactor"})
        assert "AI Ready" not in props


# ======================================================================
# create_page
# ======================================================================
class TestCreatePage:
    def test_basic_creation(self, mock_notion):
        props = {"Name": _notion_title("Test")}
        create_page("tasks", props)
        mock_notion.pages.create.assert_called_once()
        call_kwargs = mock_notion.pages.create.call_args
        assert call_kwargs.kwargs["properties"] == props
        assert "database_id" in call_kwargs.kwargs["parent"]

    def test_podcast_icon(self, mock_notion):
        props = {"Episode Title": _notion_title("Ep1")}
        create_page("podcasts", props)
        call_kwargs = mock_notion.pages.create.call_args
        assert call_kwargs.kwargs["icon"]["emoji"] == "🎧"

    def test_movie_icon(self, mock_notion):
        create_page("movies", {"Title": _notion_title("Film")})
        call_kwargs = mock_notion.pages.create.call_args
        assert call_kwargs.kwargs["icon"]["emoji"] == "🎬"

    def test_tv_show_icon(self, mock_notion):
        create_page("tv-shows", {"Title": _notion_title("Show")})
        call_kwargs = mock_notion.pages.create.call_args
        assert call_kwargs.kwargs["icon"]["emoji"] == "📺"

    def test_no_icon_for_tasks(self, mock_notion):
        create_page("tasks", {"Name": _notion_title("Task")})
        call_kwargs = mock_notion.pages.create.call_args
        assert "icon" not in call_kwargs.kwargs

    def test_create_error_raises(self, mock_notion):
        mock_notion.pages.create.side_effect = Exception("Notion API error")
        with pytest.raises(Exception, match="Notion API error"):
            create_page("tasks", {"Name": _notion_title("Test")})


# ======================================================================
# update_status
# ======================================================================
class TestUpdateStatus:
    def test_success(self, mock_notion):
        update_status("page-123", "Done")
        mock_notion.pages.update.assert_called_once_with(
            page_id="page-123",
            properties={"Status": {"status": {"name": "Done"}}},
        )

    def test_failure_returns_none(self, mock_notion):
        mock_notion.pages.update.side_effect = Exception("fail")
        result = update_status("page-123", "Done")
        assert result is None


# ======================================================================
# log_job_outcome
# ======================================================================
class TestLogJobOutcome:
    def test_success_log(self, mock_notion):
        log_job_outcome("test input", "tasks", "Success", created_url="https://notion.so/x")
        mock_notion.pages.create.assert_called_once()
        call_kwargs = mock_notion.pages.create.call_args.kwargs
        props = call_kwargs["properties"]
        assert props["Raw Input"]["title"][0]["text"]["content"] == "test input"
        assert props["Code Execution"]["status"]["name"] == "Success"
        assert props["Created Item"]["url"] == "https://notion.so/x"

    def test_error_log(self, mock_notion):
        log_job_outcome("bad input", "Unknown", "Error(s)", details="Some error")
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        assert "Some error" in props["Error Details"]["rich_text"][0]["text"]["content"]

    def test_ai_data_serialized(self, mock_notion):
        ai_data = {"Parser_Data": {"core_text": "test"}, "Extractor_Data": {"Name": "test"}}
        log_job_outcome("test", "tasks", "Success", ai_data=ai_data)
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        ai_text = props["AI Summary"]["rich_text"][0]["text"]["content"]
        assert "Parser_Data" in ai_text

    def test_project_append_adds_tag(self, mock_notion):
        log_job_outcome("test", "tasks", "Success", project_append=True)
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        assert props["Tags"]["multi_select"] == [{"name": "project-append"}]

    def test_no_project_append_omits_tag(self, mock_notion):
        log_job_outcome("test", "tasks", "Success")
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        assert "Tags" not in props


# ======================================================================
# create_cleanup_task
# ======================================================================
class TestCreateCleanupTask:
    def test_basic(self, mock_notion):
        create_cleanup_task("Fix something")
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        assert props["Name"]["title"][0]["text"]["content"] == "Fix something"
        assert props["Priority"]["select"]["name"] == "Low"
        assert props["Tags"]["multi_select"][0]["name"] == "Chore"

    def test_with_link(self, mock_notion):
        create_cleanup_task("Fix it", link_url="https://notion.so/page")
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        assert "https://notion.so/page" in props["Links"]["rich_text"][0]["text"]["content"]


# ======================================================================
# create_high_priority_task
# ======================================================================
class TestCreateHighPriorityTask:
    def test_basic(self, mock_notion):
        create_high_priority_task("Failed thought")
        props = mock_notion.pages.create.call_args.kwargs["properties"]
        assert "Classify the following thought" in props["Name"]["title"][0]["text"]["content"]
        assert props["Priority"]["select"]["name"] == "High"


# ======================================================================
# create_project_task
# ======================================================================
class TestCreateProjectTask:
    def test_creates_with_relation(self, mock_notion):
        data = {"Name": "Fix bug", "Status": "To Do", "Tags": ["Chore"], "Due Date": "2026-03-01"}
        url = create_project_task("project-id-123", data)
        assert url is not None
        call_kwargs = mock_notion.pages.create.call_args.kwargs
        assert call_kwargs["properties"]["Project"] == {"relation": [{"id": "project-id-123"}]}


# ======================================================================
# fetch_existing_page
# ======================================================================
class TestFetchExistingPage:
    def test_found(self, mock_notion):
        page = make_notion_page("found-id", "Title", "Inception")
        mock_notion.request.return_value = {"results": [page]}

        result = fetch_existing_page("movies", "Inception", "Title")
        assert result == "found-id"

    def test_not_found(self, mock_notion):
        mock_notion.request.return_value = {"results": []}
        result = fetch_existing_page("movies", "NonExistent", "Title")
        assert result is None

    def test_the_prefix_removal(self, mock_notion):
        """'The Matrix' should search for 'Matrix' (smart search)."""
        mock_notion.request.return_value = {"results": []}
        fetch_existing_page("movies", "The Matrix", "Title")
        call_body = mock_notion.request.call_args.kwargs["body"]
        assert call_body["filter"]["property"] == "Title"
        assert call_body["filter"]["title"]["contains"] == "Matrix"

    def test_no_notion_client(self):
        with patch("core.notion_utils.get_notion", return_value=None):
            result = fetch_existing_page("movies", "Test", "Title")
            assert result is None

    def test_no_db_id(self):
        with patch("core.notion_utils.get_db_id", return_value=None):
            result = fetch_existing_page("movies", "Test", "Title")
            assert result is None
