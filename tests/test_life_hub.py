"""Tests for core.life_hub — the life-data hub row push."""

from unittest.mock import MagicMock

import pytest

from core.life_hub import pull_ids, push_rows
from types import SimpleNamespace


def _settings():
    return SimpleNamespace(life_hub_url="https://hub.example/", life_hub_token="tok")


def _client(payload=None, status=200):
    resp = MagicMock()
    if payload is not None and "rows" in payload:
        payload = {**payload, "next_cursor": None}
    resp.json.return_value = payload if payload is not None else {"upserted": 1, "rejected": []}
    resp.status_code = status
    client = MagicMock()
    client.post.return_value = resp
    return client


class TestPushRows:
    def test_posts_table_columns_and_rows(self):
        client = _client()
        rows = [{"id": "27205", "status": "Finished", "updated_at": "2026-09-04T14:33:13.538Z"}]

        out = push_rows("movies", rows, settings=_settings(), client=client)

        assert out == {"upserted": 1, "rejected": []}
        url = client.post.call_args.args[0]
        assert url == "https://hub.example/v1/rows/push"
        body = client.post.call_args.kwargs["json"]
        assert body["table"] == "movies"
        assert body["rows"] == rows
        # sorted union of every row's keys
        assert body["columns"] == ["id", "status", "updated_at"]

    def test_columns_are_the_sorted_union_of_all_rows(self):
        client = _client()
        push_rows(
            "movies",
            [{"id": "1", "status": "Finished"}, {"id": "2", "tags": ["Sad"]}],
            settings=_settings(),
            client=client,
        )
        assert client.post.call_args.kwargs["json"]["columns"] == ["id", "status", "tags"]

    def test_sends_bearer_token_and_user_agent(self):
        # Cloudflare's bot protection 403s a default Python user agent before the
        # request ever reaches the Worker — the header is load-bearing.
        client = _client()
        push_rows("movies", [{"id": "1"}], settings=_settings(), client=client)
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok"
        assert headers["User-Agent"] == "synapse"
        assert headers["Content-Type"] == "application/json"

    def test_raises_on_http_error(self):
        client = _client()
        client.post.return_value.raise_for_status.side_effect = RuntimeError("500")
        with pytest.raises(RuntimeError):
            push_rows("movies", [{"id": "1"}], settings=_settings(), client=client)

    def test_unconfigured_hub_raises(self):
        with pytest.raises(RuntimeError, match="LIFE_HUB_URL"):
            push_rows(
                "movies",
                [{"id": "1"}],
                settings=SimpleNamespace(life_hub_url=None, life_hub_token=None),
                client=_client(),
            )


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
        with pytest.raises(RuntimeError, match="LIFE_HUB_URL"):
            pull_ids(
                "youtube_channels",
                settings=SimpleNamespace(life_hub_url=None, life_hub_token=None),
                client=_client(),
            )


def test_identity_read_is_bounded_and_preserves_tombstones():
    from core.life_hub import read_row

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
    from core.life_hub import insert_rows

    client = _client(payload)
    with pytest.raises(RuntimeError):
        insert_rows("items", [{"id": "item"}], settings=_settings(), client=client)
    assert client.post.call_count == 1
    assert client.post.call_args.args[0].endswith("/v1/rows/insert")


def test_patch_requires_matching_receipt_identity_and_revision():
    from core.life_hub import patch_row

    client = _client({"id": "other", "revision": {"updated_at": "new", "hub_at": "new"}})
    revision = {"updated_at": "old", "hub_at": "old"}
    with pytest.raises(RuntimeError):
        patch_row("items", "item", {"saved": 1}, revision, settings=_settings(), client=client)
    assert client.post.call_args.kwargs["json"]["expected_revision"] == revision
    assert "updated_at" not in client.post.call_args.kwargs["json"]["values"]
