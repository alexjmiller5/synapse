"""Opt-in live inference through the full journal, with an isolated synthetic hub."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests

from core import cloudflare, pipeline

_NETWORK_POST = requests.post
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("SYNAPSE_CLOUDFLARE_LIVE") != "1",
        reason="Explicit live Cloudflare evaluation only",
    ),
]


@pytest.mark.parametrize(
    "raw_text,priority",
    [
        ("Update my profile photo", "High"),
        ("Update my profile photo $ medium priority task", "Medium"),
    ],
)
def test_live_capture_is_successful_and_duplicate_is_noop(
    media_hub, monkeypatch, raw_text, priority
):
    monkeypatch.setattr(cloudflare, "requests", SimpleNamespace(post=_NETWORK_POST))
    monkeypatch.setattr(pipeline, "fetch_active_projects", lambda: ([], {}))
    monkeypatch.setattr(pipeline, "fetch_inventory_map", lambda *args: {})
    monkeypatch.setenv("AI_PROVIDER", "cloudflare")
    store = {}
    payload = {
        "capture_id": str(uuid4()),
        "workspace": "default",
        "raw_text": raw_text,
        "source": "isolated-live-test",
    }
    pipeline.run(payload, store=store)
    (task,) = media_hub.rows["tasks"].values()
    (execution,) = media_hub.rows["synapse_executions"].values()
    assert task["title"] == "Update my profile photo"
    assert task["priority"] == priority
    assert "Chore" in task["tags"]
    assert execution["code_execution"] == "Success"
    assert execution["created_item"] == f"tasks/{task['id']}"

    def unexpected(*args, **kwargs):
        pytest.fail("A completed capture repeated an inference call")

    monkeypatch.setattr(cloudflare, "generate", unexpected)
    pipeline.run(payload, store=store)
    assert len(media_hub.rows["tasks"]) == len(media_hub.rows["synapse_executions"]) == 1
