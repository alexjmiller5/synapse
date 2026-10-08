"""Tests for scripts/recept.py - the operator CLI capture producer."""

import importlib.util
from pathlib import Path
from uuid import UUID

_spec = importlib.util.spec_from_file_location(
    "recept", Path(__file__).parent.parent / "scripts" / "recept.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _sent(mocker, monkeypatch, text="Synthetic thought"):
    monkeypatch.setenv("MODAL_WEBHOOK_URL", "https://example.test/webhook")
    post = mocker.patch.object(_mod.requests, "post")
    _mod.send_event(text)
    return post.call_args.kwargs["json"]


def test_sends_a_canonical_capture_id(mocker, monkeypatch):
    # Workflow capture rejects a payload without a persisted canonical UUID.
    body = _sent(mocker, monkeypatch)
    assert str(UUID(body["capture_id"])) == body["capture_id"]
    assert body["raw_text"] == "Synthetic thought" and body["source"] == "cli"


def test_each_invocation_is_a_new_capture(mocker, monkeypatch):
    assert _sent(mocker, monkeypatch)["capture_id"] != _sent(mocker, monkeypatch)["capture_id"]
