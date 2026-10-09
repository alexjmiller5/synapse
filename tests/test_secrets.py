"""Tests for core.secrets — Notion DB ids come from the active workspace."""

from core import workspace
from core.secrets import get_db_id


def _ws(overlay):
    return workspace.use(workspace.build("t", overlay))


class TestGetDbId:
    def test_category_stanza_id(self):
        with _ws({"databases": {"tasks": {"db_id": "tasks-db"}}}):
            assert get_db_id("tasks") == "tasks-db"

    def test_helper_id_from_top_level_mapping(self):
        with _ws({"db_ids": {"projects": "projects-db"}}):
            assert get_db_id("projects") == "projects-db"

    def test_template_alone_has_no_ids(self):
        with _ws({}):
            assert get_db_id("tasks") is None
            assert get_db_id("projects") is None

    def test_hub_backed_category_has_no_db_id(self):
        """movies/tv-shows live in soma - no Notion DB, so no id to find."""
        assert get_db_id("movies") is None

    def test_unknown_category_returns_none(self):
        assert get_db_id("nope") is None

    def test_each_workspace_sees_its_own_ids(self):
        with _ws({"databases": {"tasks": {"db_id": "mine"}}}):
            with _ws({"databases": {"tasks": {"db_id": "friend"}}}):
                assert get_db_id("tasks") == "friend"
            assert get_db_id("tasks") == "mine"
