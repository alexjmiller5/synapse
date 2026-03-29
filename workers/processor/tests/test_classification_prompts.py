"""Tests for classification prompt improvements and code integration."""
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with patch.dict(
    "sys.modules",
    {
        "gcp_secrets": MagicMock(),
        "clients": MagicMock(),
        "notion_utils": MagicMock(),
    },
):
    import yaml

    PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts.yaml")


def load_prompts():
    with open(PROMPTS_PATH) as f:
        return yaml.safe_load(f)


class TestClassificationPromptRules:
    def test_movies_context_rule_exists(self):
        template = load_prompts()["categorize_template"]
        assert "priority movie" in template.lower()

    def test_tv_shows_context_rule_exists(self):
        template = load_prompts()["categorize_template"]
        assert "tv show" in template.lower()

    def test_bucket_list_context_rule_exists(self):
        template = load_prompts()["categorize_template"]
        assert "bucket list" in template.lower() or "bucket-list" in template.lower()

    def test_fun_activities_context_rule_exists(self):
        template = load_prompts()["categorize_template"]
        assert "fun-activities" in template.lower()

    def test_trips_context_rule_exists(self):
        template = load_prompts()["categorize_template"]
        assert "trips" in template.lower()


class TestCodeIntegration:
    def test_main_has_null_check(self):
        main_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(main_path) as f:
            content = f.read()
        assert "response.text is None" in content
        assert "raw_ai_text is None" in content

    def test_main_uses_retry_wrapper(self):
        main_path = os.path.join(os.path.dirname(__file__), "..", "main.py")
        with open(main_path) as f:
            content = f.read()
        assert "generate_with_retry" in content
        # run_pipeline should NOT use direct gemini_client calls
        lines = content.split("\n")
        in_run_pipeline = False
        for line in lines:
            if "def run_pipeline" in line:
                in_run_pipeline = True
            elif in_run_pipeline and line.startswith("def "):
                in_run_pipeline = False
            if in_run_pipeline and "gemini_client.models.generate_content" in line:
                raise AssertionError(
                    "run_pipeline should use generate_with_retry, not direct API calls"
                )
