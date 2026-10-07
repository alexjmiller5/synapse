"""Capture replay freezes parser decisions before any item can be written."""

import copy
from unittest.mock import Mock
from uuid import UUID

import pytest

from core.workflow import CaptureJournal, accepted_capture


CAPTURE = "e8bfc1e9-6f0e-4c15-8764-7c98d4b3a2ab"


def payload(text="Same thought"):
    return {"raw_text": text, "source": "test-client", "capture_id": CAPTURE}


def test_caller_uuid_is_preserved_and_workspace_comes_from_authentication():
    body = {**payload(), "workspace": "untrusted"}
    accepted = accepted_capture(body, "authorized")
    assert accepted["workspace"] == "authorized"
    assert accepted["capture_id"] == CAPTURE
    assert UUID(accepted["capture_id"])
    assert body["workspace"] == "untrusted"


def test_new_submissions_without_client_identity_are_distinct():
    a = accepted_capture({"raw_text": "Same"}, "sample")
    b = accepted_capture({"raw_text": "Same"}, "sample")
    assert a["capture_id"] != b["capture_id"]


@pytest.mark.parametrize("identity", ["", "../elsewhere", 1, None, CAPTURE.upper()])
def test_explicit_malformed_capture_identity_is_rejected(identity):
    with pytest.raises(ValueError):
        accepted_capture({**payload(), "capture_id": identity}, "sample")


def test_parser_result_is_retained_before_output_and_not_recomputed_after_restart():
    store = {}
    accepted = accepted_capture(payload(), "sample")
    first = CaptureJournal(store, accepted)
    parse = Mock(return_value=[{"core_text": "First"}, {"core_text": "Second"}])
    assert first.checkpoint("parsed_items", parse) == parse.return_value
    retry = CaptureJournal(store, accepted)
    changed = Mock(side_effect=AssertionError("Do not parse a retry again"))
    assert retry.checkpoint("parsed_items", changed) == parse.return_value
    changed.assert_not_called()
    result = retry.checkpoint("parsed_items", changed)
    result[0]["core_text"] = "caller mutation"
    assert retry.checkpoint("parsed_items", changed)[0]["core_text"] == "First"


def test_same_id_changed_payload_is_rejected_without_mutating_original():
    store = {}
    CaptureJournal(store, accepted_capture(payload(), "sample"))
    original = copy.deepcopy(store)
    with pytest.raises(ValueError, match="different payload"):
        CaptureJournal(store, accepted_capture(payload("Different"), "sample"))
    assert store == original


def test_identity_is_workspace_scoped_and_not_derived_from_credentials():
    store = {}
    a = CaptureJournal(store, accepted_capture(payload(), "one"))
    b = CaptureJournal(store, accepted_capture(payload(), "two"))
    a.checkpoint("parsed_items", lambda: ["A"])
    assert b.checkpoint("parsed_items", lambda: ["B"]) == ["B"]


def test_completion_is_durable_and_not_inferred_from_attempt_or_exception():
    store = {}
    accepted = accepted_capture(payload(), "sample")
    first = CaptureJournal(store, accepted)
    with pytest.raises(RuntimeError):
        first.checkpoint("parsed_items", Mock(side_effect=RuntimeError("failed")))
    assert not CaptureJournal(store, accepted).completed
    first.finish()
    assert CaptureJournal(store, accepted).completed


def test_persistence_failure_does_not_return_an_unretained_parser_result():
    class Store(dict):
        fail = False

        def __setitem__(self, key, value):
            if self.fail:
                raise OSError("store unavailable")
            super().__setitem__(key, value)

    store = Store()
    capture = CaptureJournal(store, accepted_capture(payload(), "sample"))
    store.fail = True
    with pytest.raises(OSError):
        capture.checkpoint("parsed_items", lambda: ["Never dispatched"])
    store.fail = False
    assert capture.checkpoint("parsed_items", lambda: ["Recovered"]) == ["Recovered"]


def test_selected_workflow_requires_client_identity_for_http_retry_safety():
    with pytest.raises(ValueError, match="capture_id"):
        accepted_capture({"raw_text": "One"}, "sample", require_identity=True)
    assert accepted_capture(payload(), "sample", require_identity=True)["capture_id"] == CAPTURE


def test_capture_scope_refuses_authenticated_workspace_mismatch_before_journal():
    from core.workflow import capture_scope
    from core import workspace

    store = {}
    with (
        workspace.use(workspace.build("authorized", {})),
        pytest.raises(ValueError, match="workspace"),
    ):
        with capture_scope(store, accepted_capture(payload(), "other")):
            pytest.fail("wrong workspace accepted")
    assert store == {}
