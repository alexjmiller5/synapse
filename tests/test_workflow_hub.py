"""HTTP boundary regressions for workflow reads and retry-safe writes."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from core import life_hub

SETTINGS = SimpleNamespace(life_hub_url="https://hub.example/", life_hub_token="synthetic")
STAMP = "2026-01-02T03:04:05.006Z"


def client_for(*payloads):
    client = MagicMock()
    responses = []
    for payload in payloads:
        response = MagicMock()
        response.json.return_value = payload
        responses.append(response)
    client.post.side_effect = responses
    return client


def test_reads_every_page_and_filters_deleted_only_after_completion():
    client = client_for(
        {"rows": [{"id": "a", "deleted_at": None}], "next_cursor": "opaque:1"},
        {"rows": [{"id": "b", "deleted_at": STAMP}], "next_cursor": "opaque:2"},
        {"rows": [{"id": "c", "deleted_at": None}], "next_cursor": None},
    )
    assert [
        r["id"] for r in life_hub.pull_rows("projects", ["title"], settings=SETTINGS, client=client)
    ] == ["a", "c"]
    calls = client.post.call_args_list
    assert len(calls) == 3
    assert "after" not in calls[0].kwargs["json"]
    assert calls[1].kwargs["json"]["after"] == "opaque:1"
    assert calls[2].kwargs["json"]["after"] == "opaque:2"
    assert all(c.kwargs["json"]["limit"] == 200 for c in calls)


@pytest.mark.parametrize(
    "page",
    [
        {"rows": []},
        {"rows": [], "next_cursor": ""},
        {"rows": [], "next_cursor": 1},
        {"rows": {}, "next_cursor": None},
        {"rows": [{"title": "missing id"}], "next_cursor": None},
    ],
)
def test_malformed_pages_do_not_look_complete(page):
    with pytest.raises(RuntimeError):
        life_hub.pull_rows("projects", [], settings=SETTINGS, client=client_for(page))


def test_repeated_cursor_fails_without_returning_partial_inventory():
    client = client_for({"rows": [], "next_cursor": "same"}, {"rows": [], "next_cursor": "same"})
    with pytest.raises(RuntimeError, match="cursor"):
        life_hub.pull_rows("projects", [], settings=SETTINGS, client=client)
    assert client.post.call_count == 2


def test_second_page_failure_is_not_partial_success():
    client = client_for({"rows": [{"id": "a"}], "next_cursor": "next"})
    first = client.post.side_effect
    client.post.side_effect = [next(first), requests.Timeout("synthetic timeout")]
    with pytest.raises(requests.Timeout):
        life_hub.pull_rows("projects", [], settings=SETTINGS, client=client)


def test_insert_receipt_covers_created_and_preexisting_without_upsert():
    receipt = {"inserted": ["a"], "existing": ["b"], "rejected": []}
    client = client_for(receipt)
    rows = [{"id": "a", "title": "New"}, {"id": "b", "title": "Must preserve completed"}]
    assert life_hub.insert_rows("tasks", rows, settings=SETTINGS, client=client) == receipt
    assert client.post.call_count == 1
    call = client.post.call_args
    assert call.args[0] == "https://hub.example/v1/rows/insert"
    assert call.kwargs["json"] == {"table": "tasks", "columns": ["id", "title"], "rows": rows}


@pytest.mark.parametrize(
    "receipt",
    [
        {"inserted": ["a"], "existing": [], "rejected": [{"id": "b"}]},
        {"inserted": ["a"], "existing": ["b"], "rejected": [{"id": "b"}]},
        {"inserted": ["a"], "existing": [], "rejected": []},
        {"inserted": ["a"], "existing": ["a"], "rejected": []},
        {"inserted": ["a", "foreign"], "existing": [], "rejected": []},
        {"inserted": ["a", {}], "existing": [], "rejected": []},
        {"upserted": 2},
    ],
)
def test_partial_or_ambiguous_insert_receipt_fails(receipt):
    with pytest.raises(RuntimeError):
        life_hub.insert_rows(
            "tasks", [{"id": "a"}, {"id": "b"}], settings=SETTINGS, client=client_for(receipt)
        )


@pytest.mark.parametrize(
    "rows", [[{"title": "No identity"}], [{"id": "a"}, {"id": "a"}], [{"id": ""}]]
)
def test_invalid_insert_identity_rejected_before_network(rows):
    client = client_for()
    with pytest.raises(ValueError):
        life_hub.insert_rows("tasks", rows, settings=SETTINGS, client=client)
    client.post.assert_not_called()


def test_patch_uses_exact_revision_and_never_falls_back_on_conflict():
    revision = {"updated_at": STAMP, "hub_at": STAMP}
    client = client_for({"id": "a", "revision": revision})
    assert (
        life_hub.patch_row(
            "tasks", "a", {"status": "Completed"}, revision, settings=SETTINGS, client=client
        )["id"]
        == "a"
    )
    assert client.post.call_args.args[0].endswith("/v1/rows/patch")
    assert client.post.call_args.kwargs["json"]["expected_revision"] == revision
    failure = MagicMock()
    failure.raise_for_status.side_effect = requests.HTTPError("409 revision_conflict")
    client.post.side_effect = [failure]
    with pytest.raises(requests.HTTPError):
        life_hub.patch_row(
            "tasks", "a", {"status": "Completed"}, revision, settings=SETTINGS, client=client
        )
    assert client.post.call_count == 2
    assert all(c.args[0].endswith("/v1/rows/patch") for c in client.post.call_args_list)


@pytest.mark.parametrize(
    "reply",
    [
        {"id": "other", "revision": {"updated_at": STAMP, "hub_at": STAMP}},
        {"id": "a"},
        {"id": "a", "revision": {"updated_at": "yesterday", "hub_at": None}},
    ],
)
def test_invalid_patch_receipt_is_not_success(reply):
    with pytest.raises(RuntimeError):
        life_hub.patch_row(
            "tasks",
            "a",
            {"status": "Completed"},
            {"updated_at": STAMP, "hub_at": STAMP},
            settings=SETTINGS,
            client=client_for(reply),
        )


def test_later_page_tombstone_replaces_earlier_live_row():
    client = client_for(
        {"rows": [{"id": "a", "deleted_at": None, "title": "Earlier"}], "next_cursor": "next"},
        {
            "rows": [
                {"id": "a", "deleted_at": STAMP, "title": "Earlier"},
                {"id": "b", "deleted_at": None},
            ],
            "next_cursor": None,
        },
    )
    assert life_hub.pull_ids("tasks", settings=SETTINGS, client=client) == {"b"}
