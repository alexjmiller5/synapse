"""Tests for core.secrets — env-var override with databases.yaml fallback."""

from core.config import DATABASES
from core.secrets import get_db_id


class TestGetDbId:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("NOTION_TASKS_DB_ID", "env-override-id")
        assert get_db_id("tasks") == "env-override-id"

    def test_unset_env_falls_back_to_yaml_stanza(self, monkeypatch):
        monkeypatch.delenv("NOTION_TASKS_DB_ID", raising=False)
        assert get_db_id("tasks") == DATABASES["databases"]["tasks"]["db_id"]
        assert get_db_id("tasks")  # non-empty

    def test_kebab_case_category(self, monkeypatch):
        monkeypatch.delenv("NOTION_TV_SHOWS_DB_ID", raising=False)
        assert get_db_id("tv-shows") == DATABASES["databases"]["tv-shows"]["db_id"]

    def test_non_category_id_from_top_level_mapping(self, monkeypatch):
        monkeypatch.delenv("NOTION_PROJECTS_DB_ID", raising=False)
        assert get_db_id("projects") == DATABASES["db_ids"]["projects"]

    def test_unknown_category_returns_none(self, monkeypatch):
        monkeypatch.delenv("NOTION_NOPE_DB_ID", raising=False)
        assert get_db_id("nope") is None
