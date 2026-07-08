"""Tests for core.timeutils — all task/date creation is pinned to Eastern time.

The old code used server-local datetime.now()/date.today(), which produced
tomorrow's date for late-night captures (server in UTC).
"""

from datetime import date, datetime, timezone
from unittest.mock import patch

from core import timeutils
from core.notion_utils import create_cleanup_task
from helpers import sent_props


class _FakeDatetime(datetime):
    """datetime whose now(tz) is anchored to a fixed UTC instant."""

    _fixed_utc = None

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls._fixed_utc.replace(tzinfo=None)
        return cls._fixed_utc.astimezone(tz)


def _freeze(monkeypatch, fixed_utc):
    _FakeDatetime._fixed_utc = fixed_utc
    monkeypatch.setattr(timeutils, "datetime", _FakeDatetime)


class TestTodayEastern:
    def test_late_night_utc_lands_on_previous_eastern_date(self, monkeypatch):
        # 2026-07-05 02:30 UTC == 2026-07-04 22:30 EDT (UTC-4)
        _freeze(monkeypatch, datetime(2026, 7, 5, 2, 30, tzinfo=timezone.utc))
        assert timeutils.today_eastern() == date(2026, 7, 4)

    def test_winter_est_offset(self, monkeypatch):
        # 2026-01-10 04:59 UTC == 2026-01-09 23:59 EST (UTC-5)
        _freeze(monkeypatch, datetime(2026, 1, 10, 4, 59, tzinfo=timezone.utc))
        assert timeutils.today_eastern() == date(2026, 1, 9)

    def test_daytime_matches(self, monkeypatch):
        # 2026-07-05 16:00 UTC == 2026-07-05 12:00 EDT — same date
        _freeze(monkeypatch, datetime(2026, 7, 5, 16, 0, tzinfo=timezone.utc))
        assert timeutils.today_eastern() == date(2026, 7, 5)


class TestNowEastern:
    def test_is_timezone_aware_eastern(self):
        now = timeutils.now_eastern()
        assert now.tzinfo is not None
        assert str(now.tzinfo) == "America/New_York"


class TestWiring:
    def test_cleanup_task_due_date_uses_eastern_today(self, mock_notion):
        with patch("core.notion_utils.today_eastern", return_value=date(2026, 7, 4)):
            create_cleanup_task("Fix something")
        props = sent_props(mock_notion.pages.create, "tasks")
        assert props["Due Date"]["date"]["start"] == "2026-07-04"
