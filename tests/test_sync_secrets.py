"""Tests for scripts/sync_secrets.py — the dotenv parser feeding the Modal secret."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sync_secrets", Path(__file__).parent.parent / "scripts" / "sync_secrets.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
parse_dotenv = _mod.parse_dotenv


class TestParseDotenv:
    def test_basic_pairs(self):
        assert parse_dotenv("A=1\nB=two") == {"A": "1", "B": "two"}

    def test_skips_comments_and_blank_lines(self):
        assert parse_dotenv("# comment\n\nA=1\n   \n# B=nope") == {"A": "1"}

    def test_strips_matched_quotes(self):
        assert parse_dotenv("A=\"quoted\"\nB='single'") == {"A": "quoted", "B": "single"}

    def test_keeps_unmatched_quote(self):
        assert parse_dotenv('A="half') == {"A": '"half'}

    def test_value_may_contain_equals(self):
        assert parse_dotenv("URL=https://x.test/?a=b&c=d") == {"URL": "https://x.test/?a=b&c=d"}

    def test_whitespace_trimmed(self):
        assert parse_dotenv("  A = 1  ") == {"A": "1"}

    def test_skips_lines_without_equals(self):
        assert parse_dotenv("garbage\nA=1") == {"A": "1"}
