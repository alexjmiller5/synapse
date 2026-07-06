"""Secret access — env vars only (Modal Secret in the cloud, `op run` locally).

Keeps the legacy kebab-case secret-id API so call sites are unchanged:
get_secret("gemini-api-key") reads the GEMINI_API_KEY env var.
"""

import os

from core.config import DATABASES


def get_secret(secret_id, version="latest"):
    return os.environ.get(secret_id.upper().replace("-", "_"))


def get_db_id(category):
    """Fetches the DB ID for a category based on convention."""
    return get_secret(f"notion-{category}-db-id")


def _prefetch_db_ids():
    ids = {}
    for cat in list(DATABASES.get("databases", {}).keys()) + [
        "logs",
        "youtube-channels",
        "trips",
        "projects",
        "notes",
    ]:
        val = get_db_id(cat)
        if val:
            ids[cat] = val
    return ids


DATABASE_IDS = _prefetch_db_ids()
