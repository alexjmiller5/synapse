"""Tests for the webhook payload validation (core.pipeline.payload_error).

app.py's webhook endpoint calls payload_error() and returns 422 when it
reports a problem — the validation logic itself is pure and tested here.
"""

from core.pipeline import payload_error


class TestPayloadError:
    def test_valid_payload(self):
        assert payload_error({"raw_text": "Buy eggs"}) is None

    def test_valid_payload_with_extra_fields(self):
        assert payload_error({"raw_text": "Buy eggs", "other": 1}) is None

    def test_missing_raw_text(self):
        assert "raw_text" in payload_error({"other_field": "value"})

    def test_empty_raw_text(self):
        assert payload_error({"raw_text": ""}) is not None

    def test_whitespace_raw_text(self):
        assert payload_error({"raw_text": "   \n"}) is not None

    def test_non_string_raw_text(self):
        assert payload_error({"raw_text": 42}) is not None

    def test_none_payload(self):
        assert payload_error(None) is not None

    def test_non_dict_payload(self):
        assert payload_error(["raw_text"]) is not None

    def test_unicode_ok(self):
        assert payload_error({"raw_text": "Café résumé naïve"}) is None
