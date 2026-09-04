"""Tests for core.life_hub — the life-data hub row push."""

from unittest.mock import MagicMock

import pytest

from core.life_hub import push_rows
from core.settings import Settings


def _settings():
    return Settings(life_hub_url="https://hub.example/", life_hub_token="tok")


def _client(payload=None, status=200):
    resp = MagicMock()
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
                settings=Settings(life_hub_url=None, life_hub_token=None),
                client=_client(),
            )
