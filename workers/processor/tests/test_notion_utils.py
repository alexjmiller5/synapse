"""Tests for notion_utils.py — truncation guards and date validation."""
import sys
import os
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(
    "sys.modules",
    {
        "config": MagicMock(DATABASES={"databases": {}}),
        "gcp_secrets": MagicMock(),
        "clients": MagicMock(),
    },
):
    from notion_utils import _notion_title, _notion_rich_text, _notion_date


class TestTitleTruncation:
    def test_short_title_unchanged(self):
        result = _notion_title("Hello World")
        assert result["title"][0]["text"]["content"] == "Hello World"

    def test_exactly_2000_chars_unchanged(self):
        text = "a" * 2000
        result = _notion_title(text)
        assert len(result["title"][0]["text"]["content"]) == 2000

    def test_title_over_2000_truncated(self):
        text = "x" * 3000
        result = _notion_title(text)
        assert len(result["title"][0]["text"]["content"]) == 2000

    def test_title_much_longer_truncated(self):
        text = "a" * 10000
        result = _notion_title(text)
        assert len(result["title"][0]["text"]["content"]) == 2000

    def test_empty_title(self):
        result = _notion_title("")
        assert result["title"][0]["text"]["content"] == ""


class TestRichTextTruncation:
    def test_short_rich_text_unchanged(self):
        result = _notion_rich_text("Hello")
        assert result["rich_text"][0]["text"]["content"] == "Hello"

    def test_rich_text_over_2000_truncated(self):
        text = "y" * 5000
        result = _notion_rich_text(text)
        assert len(result["rich_text"][0]["text"]["content"]) == 2000

    def test_none_returns_empty_list(self):
        result = _notion_rich_text(None)
        assert result == {"rich_text": []}

    def test_empty_string_returns_empty_list(self):
        result = _notion_rich_text("")
        assert result == {"rich_text": []}

    def test_numeric_value_converted_and_truncated(self):
        result = _notion_rich_text(12345)
        assert result["rich_text"][0]["text"]["content"] == "12345"


class TestDateValidation:
    def test_valid_date_passes(self):
        result = _notion_date("2026-03-15")
        assert result == {"date": {"start": "2026-03-15"}}

    def test_valid_date_with_time(self):
        result = _notion_date("2026-03-15T10:30:00")
        assert result["date"]["start"] == "2026-03-15T10:30:00"

    def test_invalid_feb_29_non_leap_year(self):
        result = _notion_date("2026-02-29")
        assert result["date"]["start"] == date.today().isoformat()

    def test_invalid_date_month_13(self):
        result = _notion_date("2026-13-01")
        assert result["date"]["start"] == date.today().isoformat()

    def test_invalid_date_day_32(self):
        result = _notion_date("2026-01-32")
        assert result["date"]["start"] == date.today().isoformat()

    def test_invalid_date_garbage_string(self):
        result = _notion_date("not-a-date")
        assert result["date"]["start"] == date.today().isoformat()

    def test_none_date(self):
        result = _notion_date(None)
        assert result == {"date": {"start": None}}

    def test_valid_leap_year_feb_29(self):
        result = _notion_date("2028-02-29")
        assert result == {"date": {"start": "2028-02-29"}}
