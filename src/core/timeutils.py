"""Eastern-pinned clock helpers.

All user-facing dates (due dates, "today" in prompts, listened/watched dates)
must be computed in the user's timezone, not the server's — a late-night
capture in NYC must not land on tomorrow's date because the server runs UTC.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def now_eastern() -> datetime:
    return datetime.now(EASTERN)


def today_eastern():
    return now_eastern().date()
