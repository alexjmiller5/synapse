"""Tests for core.soma_hub — the soma hub row API client."""

from unittest.mock import MagicMock

import pytest

from core.soma_hub import pull_ids
from types import SimpleNamespace


def _settings():
    return SimpleNamespace(soma_hub_url="https://hub.example/", soma_hub_token="tok")


def _client(payload=None, status=200):
    resp = MagicMock()
    if payload is not None and "rows" in payload:
        payload = {**payload, "next_cursor": None}
    resp.json.return_value = payload if payload is not None else {"upserted": 1, "rejected": []}
    resp.status_code = status
    client = MagicMock()
    client.post.return_value = resp
    return client


class TestPullIds:
    def test_posts_table_columns_and_since(self):
        client = _client(
            payload={"rows": [{"id": "UC1", "deleted_at": None}, {"id": "UC2", "deleted_at": None}]}
        )

        out = pull_ids("youtube_channels", settings=_settings(), client=client)

        assert out == {"UC1", "UC2"}
        url = client.post.call_args.args[0]
        assert url == "https://hub.example/v1/rows/pull"
        body = client.post.call_args.kwargs["json"]
        assert body == {
            "table": "youtube_channels",
            "columns": ["id", "deleted_at"],
            "since": "",
            "limit": 200,
        }
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok"
        assert headers["User-Agent"] == "synapse"

    def test_drops_deleted_rows(self):
        client = _client(
            payload={
                "rows": [
                    {"id": "UC1", "deleted_at": None},
                    {"id": "UC2", "deleted_at": "2026-09-01T00:00:00.000Z"},
                ]
            }
        )
        out = pull_ids("youtube_channels", settings=_settings(), client=client)
        assert out == {"UC1"}

    def test_unconfigured_hub_raises(self):
        with pytest.raises(RuntimeError, match="SOMA_HUB_URL"):
            pull_ids(
                "youtube_channels",
                settings=SimpleNamespace(soma_hub_url=None, soma_hub_token=None),
                client=_client(),
            )


def test_identity_read_is_bounded_and_preserves_tombstones():
    from core.soma_hub import read_row

    row = {"id": "item", "updated_at": "revision", "hub_at": "hub", "deleted_at": "gone"}
    client = _client({"rows": [row], "next_cursor": None})
    assert read_row("items", "item", ["status"], settings=_settings(), client=client) == row
    body = client.post.call_args.kwargs["json"]
    assert body["where"] == {"id": "item"} and body["limit"] == 2
    assert "deleted_at" in body["columns"]


@pytest.mark.parametrize(
    "payload",
    [
        {"inserted": [], "existing": [], "rejected": []},
        {"inserted": ["item"], "existing": ["item"], "rejected": []},
        {"inserted": ["other"], "existing": [], "rejected": []},
    ],
)
def test_invalid_insert_receipts_never_become_success_or_push_fallback(payload):
    from core.soma_hub import insert_rows

    client = _client(payload)
    with pytest.raises(RuntimeError):
        insert_rows("items", [{"id": "item"}], settings=_settings(), client=client)
    assert client.post.call_count == 1
    assert client.post.call_args.args[0].endswith("/v1/rows/insert")


def test_patch_requires_matching_receipt_identity_and_revision():
    from core.soma_hub import patch_row

    client = _client({"id": "other", "revision": {"updated_at": "new", "hub_at": "new"}})
    revision = {"updated_at": "old", "hub_at": "old"}
    with pytest.raises(RuntimeError):
        patch_row("items", "item", {"saved": 1}, revision, settings=_settings(), client=client)
    assert client.post.call_args.kwargs["json"]["expected_revision"] == revision
    assert "updated_at" not in client.post.call_args.kwargs["json"]["values"]
