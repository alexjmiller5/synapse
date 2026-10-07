"""Exercise the actual Modal endpoint functions without starting Modal."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from core import capture_clients, media_capture


def test_gateway_endpoint_queues_once_and_general_endpoint_refuses_its_token(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "synthetic_app", Path(__file__).parents[1] / "app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = {}
    issued = capture_clients.issue_gateway(store, "Fixture", "fixture", ["articles"], ["saved"])
    authorization = "Bearer " + issued["token"]
    payload = {
        "action": "submit",
        "subject": "a" * 64,
        "allowed_fields": ["saved"],
        "request": {
            "request_id": "11111111-1111-4111-8111-111111111111",
            "input": {"url": "https://example.test/a"},
            "intent": "save",
        },
    }
    queued = []

    def remote(value):
        assert value["workspace"] == "fixture"
        op = value["media_operation"]
        return media_capture.submit_capture(store, op["caller"], op["request"])

    monkeypatch.setattr(module, "_state", lambda: store)
    monkeypatch.setattr(module, "process", SimpleNamespace(remote=remote, spawn=queued.append))
    endpoint = module.media_capture_endpoint.get_raw_f()
    assert endpoint(payload, authorization)["state"] == "received"
    assert len(queued) == 1
    assert queued[0]["media_operation"]["action"] == "process"
    read = {k: v for k, v in payload.items() if k != "request"}
    read.update(action="get", request_id=payload["request"]["request_id"])
    monkeypatch.setattr(
        module, "process", SimpleNamespace(remote=lambda _: (_ for _ in ()).throw(AssertionError()))
    )
    assert endpoint(read, authorization)["state"] == "received"
    assert module.capture.get_raw_f()({"raw_text": "make a task"}, authorization).status_code == 401
    capture_clients.revoke(store, issued["client_id"])
    assert endpoint(read, authorization).status_code == 401
