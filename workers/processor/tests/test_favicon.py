"""Tests for bookmark favicon URL generation and icon logic in create_page."""

import sys
import os
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse

# Add processor directory to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helper: extract domain the same way the production code does ──


def _extract_domain(url: str) -> str | None:
    """Mirror the domain extraction logic from notion_utils.py create_page."""
    parsed = urlparse(url)
    return parsed.netloc or None


# ── Helper: build favicon URL the same way the production code does ──


FAVICON_TEMPLATE = (
    "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON"
    "&fallback_opts=TYPE,SIZE,URL&url=http://{domain}&size=128"
)


def _build_favicon_url(domain: str) -> str:
    return FAVICON_TEMPLATE.format(domain=domain)


# ══════════════════════════════════════════════════
# 1. Domain extraction tests
# ══════════════════════════════════════════════════


class TestDomainExtraction:
    """Verify correct domain extraction for various URL formats."""

    def test_standard_https_url(self):
        assert _extract_domain("https://example.com/page") == "example.com"

    def test_url_with_subdomain(self):
        assert _extract_domain("https://docs.github.com/en") == "docs.github.com"

    def test_url_with_www(self):
        assert _extract_domain("https://www.nytimes.com/article") == "www.nytimes.com"

    def test_url_with_port(self):
        assert _extract_domain("https://localhost:3000/api") == "localhost:3000"

    def test_url_with_query_params(self):
        assert _extract_domain("https://example.com/search?q=test&page=2") == "example.com"

    def test_url_with_fragment(self):
        assert _extract_domain("https://example.com/page#section") == "example.com"

    def test_http_url(self):
        assert _extract_domain("http://insecure-site.org/path") == "insecure-site.org"

    def test_url_without_path(self):
        assert _extract_domain("https://github.com") == "github.com"

    def test_empty_string(self):
        assert _extract_domain("") is None

    def test_bare_domain_no_protocol(self):
        # urlparse without protocol puts everything in path, not netloc
        # Synapse always prepends https:// so this edge case shouldn't occur
        result = _extract_domain("example.com")
        assert result is None  # no netloc without protocol


# ══════════════════════════════════════════════════
# 2. Favicon URL generation tests
# ══════════════════════════════════════════════════


class TestFaviconUrlGeneration:
    """Verify the Google favicon service URL is built correctly."""

    def test_simple_domain(self):
        url = _build_favicon_url("github.com")
        assert "url=http://github.com" in url
        assert "size=128" in url
        assert url.startswith("https://t2.gstatic.com/faviconV2")

    def test_subdomain(self):
        url = _build_favicon_url("docs.python.org")
        assert "url=http://docs.python.org" in url

    def test_domain_with_port(self):
        url = _build_favicon_url("localhost:3000")
        assert "url=http://localhost:3000" in url


# ══════════════════════════════════════════════════
# 3. Integration: create_page icon logic
# ══════════════════════════════════════════════════


class TestCreatePageBookmarkIcon:
    """Test that create_page sets the correct icon for bookmarks."""

    @patch("notion_utils.notion")
    @patch("notion_utils.get_db_id", return_value="fake-db-id")
    def test_bookmark_with_github_url_gets_custom_emoji(self, mock_db_id, mock_notion):
        """When creating a bookmark with a GitHub URL, the icon should be the github-light custom emoji."""
        mock_notion.pages.create.return_value = {"id": "test-id", "url": "https://notion.so/test"}

        from notion_utils import create_page

        props = {
            "Description": {"title": [{"text": {"content": "Test Bookmark"}}]},
            "URL": {"url": "https://github.com/some/repo"},
        }

        create_page("bookmarks", props)

        call_kwargs = mock_notion.pages.create.call_args[1]
        assert "icon" in call_kwargs
        assert call_kwargs["icon"]["type"] == "custom_emoji"
        assert call_kwargs["icon"]["custom_emoji"]["id"] == "2d103953-a8af-8072-b828-007aa3901d27"

    @patch("notion_utils.notion")
    @patch("notion_utils.get_db_id", return_value="fake-db-id")
    def test_bookmark_with_non_github_url_gets_favicon(self, mock_db_id, mock_notion):
        """When creating a bookmark with a non-GitHub URL, the icon should be the site's favicon."""
        mock_notion.pages.create.return_value = {"id": "test-id", "url": "https://notion.so/test"}

        from notion_utils import create_page

        props = {
            "Description": {"title": [{"text": {"content": "Test Bookmark"}}]},
            "URL": {"url": "https://docs.determinate.systems/guide"},
        }

        create_page("bookmarks", props)

        call_kwargs = mock_notion.pages.create.call_args[1]
        assert "icon" in call_kwargs
        assert call_kwargs["icon"]["type"] == "external"
        assert "docs.determinate.systems" in call_kwargs["icon"]["external"]["url"]
        assert "faviconV2" in call_kwargs["icon"]["external"]["url"]
        # Verify we use https:// in the URL param
        assert "url=https://" in call_kwargs["icon"]["external"]["url"]

    @patch("notion_utils.notion")
    @patch("notion_utils.get_db_id", return_value="fake-db-id")
    def test_bookmark_without_url_gets_no_icon(self, mock_db_id, mock_notion):
        """When creating a bookmark without a URL, no icon should be set."""
        mock_notion.pages.create.return_value = {"id": "test-id", "url": "https://notion.so/test"}

        from notion_utils import create_page

        props = {
            "Description": {"title": [{"text": {"content": "No URL Bookmark"}}]},
        }

        create_page("bookmarks", props)

        call_kwargs = mock_notion.pages.create.call_args[1]
        assert "icon" not in call_kwargs

    @patch("notion_utils.notion")
    @patch("notion_utils.get_db_id", return_value="fake-db-id")
    def test_podcasts_still_get_emoji_icon(self, mock_db_id, mock_notion):
        """Ensure existing emoji icon logic for podcasts is unchanged."""
        mock_notion.pages.create.return_value = {"id": "test-id", "url": "https://notion.so/test"}

        from notion_utils import create_page

        props = {"Name": {"title": [{"text": {"content": "Test Podcast"}}]}}

        create_page("podcasts", props)

        call_kwargs = mock_notion.pages.create.call_args[1]
        assert call_kwargs["icon"] == {"type": "emoji", "emoji": "🎧"}

    @patch("notion_utils.notion")
    @patch("notion_utils.get_db_id", return_value="fake-db-id")
    def test_other_category_gets_no_icon(self, mock_db_id, mock_notion):
        """Categories without icon logic should not have an icon set."""
        mock_notion.pages.create.return_value = {"id": "test-id", "url": "https://notion.so/test"}

        from notion_utils import create_page

        props = {"Name": {"title": [{"text": {"content": "Test Task"}}]}}

        create_page("tasks", props)

        call_kwargs = mock_notion.pages.create.call_args[1]
        assert "icon" not in call_kwargs
