"""Notion database ids, read from the active workspace (core/workspace.py)."""

from core.config import DATABASES


def get_db_id(category):
    """A category's Notion DB id (its stanza's `db_id`), else a helper DB's id
    from the top-level `db_ids` mapping (logs, projects). Ids are
    workspace data, set in the workspace's overlay - never in the template."""
    stanza = DATABASES.get("databases", {}).get(category) or {}
    return stanza.get("db_id") or DATABASES.get("db_ids", {}).get(category)
