"""Secret access — env vars only (Modal Secret in the cloud, `op run` locally).

Keeps the legacy kebab-case secret-id API so call sites are unchanged:
get_secret("gemini-api-key") reads the GEMINI_API_KEY env var.
"""

import os

from core.config import DATABASES


def get_secret(secret_id, version="latest"):
    return os.environ.get(secret_id.upper().replace("-", "_"))


def get_db_id(category):
    """DB id for a category: NOTION_<X>_DB_ID env var wins, else committed config.

    Ids are config, not secrets (repo is private). They currently live in
    databases.yaml — a future refactor may move them into native pydantic
    config; only this function needs to know where they live.
    """
    env_val = get_secret(f"notion-{category}-db-id")
    if env_val:
        return env_val
    stanza = DATABASES.get("databases", {}).get(category) or {}
    return stanza.get("db_id") or DATABASES.get("db_ids", {}).get(category)


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
