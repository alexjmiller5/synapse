"""Eastern-pinned clock helpers.

All user-facing dates (due dates, "today" in prompts, listened/watched dates)
must be computed in the user's timezone, not the server's — a late-night
capture in NYC must not land on tomorrow's date because the server runs UTC.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def now_eastern() -> datetime:
    return datetime.now(EASTERN)


def today_eastern():
    return now_eastern().date()


def now_utc_iso_ms() -> str:
    """Now as ISO 8601 UTC with milliseconds - life-data's timestamp format.

    Sync ordering there is a lexicographic compare of these strings, so the
    shape (millisecond precision, trailing 'Z') is load-bearing, not cosmetic.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
