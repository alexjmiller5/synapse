"""Durable media-only capture receipts over the existing serialized worker."""

from copy import deepcopy

import pytest

from core import capture_clients, media_capture
from core.media_save import SaveReceipt

REQUEST = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "input": {"text": "save a synthetic video"},
    "intent": "save",
}


def fixture():
    store = {}
    issued = capture_clients.issue_gateway(
        store, "Synthetic gateway", "fixture", ["youtube-videos"], ["saved", "status"]
    )
    client = capture_clients.authenticate(store, "Bearer " + issued["token"])
    caller = {**client, "subject": "a" * 64, "allowed_fields": ["saved", "status"]}
    return store, caller


def test_duplicate_request_is_stable_and_changed_input_conflicts():
    store, caller = fixture()
    first = media_capture.submit_capture(store, caller, REQUEST)
    assert first == {"request_id": REQUEST["request_id"], "state": "received"}
    assert media_capture.submit_capture(store, caller, deepcopy(REQUEST)) == first
    with pytest.raises(media_capture.Conflict):
        media_capture.submit_capture(store, caller, {**REQUEST, "intent": "record_consumption"})


def test_wrong_subject_or_gateway_cannot_read_or_replay_receipt():
    store, caller = fixture()
    media_capture.submit_capture(store, caller, REQUEST)
    with pytest.raises(media_capture.NotFound):
        media_capture.get_capture(store, {**caller, "subject": "b" * 64}, REQUEST["request_id"])
    _, other = fixture()
    other["client_id"] = "another-gateway"
    with pytest.raises(capture_clients.Unauthorized):
        media_capture.get_capture(store, other, REQUEST["request_id"])


def test_saved_only_after_writer_receipt_and_no_replay_after_success():
    store, caller = fixture()
    media_capture.submit_capture(store, caller, REQUEST)
    observed = []

    def resolve(request, categories, fields, **kwargs):
        observed.append(media_capture.get_capture(store, caller, REQUEST["request_id"])["state"])
        assert categories == ["youtube-videos"] and fields == ["saved", "status"]
        return "youtubeVideo", SaveReceipt("saved", "video-1", {"updated_at": "r", "hub_at": "r"})

    result = media_capture.process_capture(store, caller, REQUEST["request_id"], resolver=resolve)
    assert result == {
        "request_id": REQUEST["request_id"],
        "state": "saved",
        "item": {"kind": "youtubeVideo", "id": "video-1"},
    }
    assert (
        media_capture.process_capture(store, caller, REQUEST["request_id"], resolver=resolve)
        == result
    )
    assert observed == ["processing"]


def test_interrupted_or_uncertain_processing_never_replays_a_write():
    store, caller = fixture()
    media_capture.submit_capture(store, caller, REQUEST)

    def interrupted(*args, **kwargs):
        raise SystemExit("worker interrupted after mutation")

    with pytest.raises(SystemExit):
        media_capture.process_capture(store, caller, REQUEST["request_id"], resolver=interrupted)
    result = media_capture.process_capture(
        store,
        caller,
        REQUEST["request_id"],
        resolver=lambda *args: pytest.fail("must reconcile before writing again"),
    )
    assert result["state"] == "uncertain"


def test_general_device_token_cannot_delegate_and_gateway_revocation_blocks_receipts():
    store, caller = fixture()
    regular = capture_clients.issue(store, "Device", "fixture")
    device = capture_clients.authenticate(store, "Bearer " + regular["token"])
    with pytest.raises(capture_clients.Unauthorized):
        media_capture.submit_capture(store, {**device, "subject": "a" * 64}, REQUEST)
    media_capture.submit_capture(store, caller, REQUEST)
    capture_clients.revoke(store, caller["client_id"])
    with pytest.raises(capture_clients.Unauthorized):
        media_capture.get_capture(store, caller, REQUEST["request_id"])


def test_receipt_rejects_forged_category_permissions_and_extra_fields():
    store, caller = fixture()
    with pytest.raises(capture_clients.Unauthorized):
        media_capture.submit_capture(
            store, {**caller, "allowed_fields": ["saved", "admin"]}, REQUEST
        )
    with pytest.raises(media_capture.InvalidRequest):
        media_capture.submit_capture(store, caller, {**REQUEST, "workspace": "other"})


def test_classifier_cannot_escape_into_task_writer(monkeypatch):
    from core import media_resolution

    monkeypatch.setattr(media_resolution, "classify", lambda text: "tasks")
    monkeypatch.setattr(
        media_resolution,
        "extract",
        lambda *args: pytest.fail("disallowed category must stop before extraction"),
    )
    kind, receipt = media_resolution.resolve_capture(
        REQUEST, ["youtube-videos"], ["saved", "status"]
    )
    assert kind is None and receipt.state == "needs_review"


def test_inferred_ungranted_field_is_reviewed_before_any_writer(monkeypatch):
    from core import media_resolution

    monkeypatch.setattr(media_resolution, "classify", lambda text: "youtube-videos")
    monkeypatch.setattr(
        media_resolution,
        "extract",
        lambda *args: {"Video URL": "https://youtu.be/abc123", "Status": "Finished"},
    )
    monkeypatch.setattr(
        media_resolution,
        "write_resolved",
        lambda *args, **kwargs: pytest.fail("must not write forbidden field"),
    )
    kind, receipt = media_resolution.resolve_capture(REQUEST, ["youtube-videos"], ["saved"])
    assert receipt.state == "needs_review"


def test_gateway_envelope_rejects_device_credentials_and_caller_workspace_injection():
    from core.media_capture import gateway_caller

    store, caller = fixture()
    issued = capture_clients.issue(store, "Ordinary device", "fixture")
    with pytest.raises(capture_clients.Unauthorized):
        gateway_caller(
            store, "Bearer " + issued["token"], {"subject": "a" * 64, "allowed_fields": ["saved"]}
        )
    with pytest.raises(media_capture.InvalidRequest):
        media_capture.validate_envelope(
            {
                "action": "get",
                "subject": "a" * 64,
                "allowed_fields": [],
                "request_id": REQUEST["request_id"],
                "workspace": "other",
            }
        )


@pytest.mark.parametrize(
    "category,kind,url,data",
    [
        (
            "articles",
            "article",
            "https://example.test/article",
            {"Title": "Example", "URL": "https://example.test/article"},
        ),
        (
            "podcasts",
            "podcastEpisode",
            "https://open.spotify.com/episode/example",
            {"Episode Title": "Example", "URL": "https://open.spotify.com/episode/example"},
        ),
    ],
)
def test_url_submission_reaches_committed_media_receipt(
    category, kind, url, data, monkeypatch, media_hub
):
    from core import media_resolution

    store = {}
    issued = capture_clients.issue_gateway(
        store, "Synthetic gateway", "fixture", [category], ["saved", "status"]
    )
    caller = {
        **capture_clients.authenticate(store, "Bearer " + issued["token"]),
        "subject": "a" * 64,
        "allowed_fields": ["saved", "status"],
    }
    monkeypatch.setattr(media_resolution, "extract", lambda *args: data)
    request = {**REQUEST, "input": {"url": url}}
    media_capture.submit_capture(store, caller, request)
    result = media_capture.process_capture(store, caller, request["request_id"])
    assert result["state"] == "saved" and result["item"] == {"kind": kind, "id": url}
    table = "articles" if category == "articles" else "podcast_episodes"
    assert media_hub.rows[table][url]["saved"] == 1


def test_uncertain_receipt_reconciles_committed_identity_without_replaying_write(
    monkeypatch, media_hub
):
    from core import media_resolution

    store = {}
    issued = capture_clients.issue_gateway(
        store, "Synthetic gateway", "fixture", ["articles"], ["saved", "status"]
    )
    caller = {
        **capture_clients.authenticate(store, "Bearer " + issued["token"]),
        "subject": "a" * 64,
        "allowed_fields": ["saved", "status"],
    }
    url = "https://example.test/article"
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"Title": "Example", "URL": url})
    request = {**REQUEST, "input": {"url": url}}
    media_capture.submit_capture(store, caller, request)
    media_hub.lose_reply = "insert"
    assert (
        media_capture.process_capture(store, caller, request["request_id"])["state"] == "uncertain"
    )
    monkeypatch.setattr(
        media_resolution,
        "extract",
        lambda *args: pytest.fail("must not resolve or mutate a second time"),
    )
    assert media_capture.process_capture(store, caller, request["request_id"])["state"] == "saved"
    assert len(media_hub.writes) == 1


def test_explicit_clear_survives_extraction_defaults(monkeypatch, media_hub):
    from core import media_resolution
    from test_media_save import existing

    url = "https://example.test/article"
    media_hub.rows["articles"] = {url: {**existing(), "id": url}}
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"Title": "Example", "URL": url})
    request = {**REQUEST, "input": {"url": url}, "fields": {"note": None}}
    kind, receipt = media_resolution.resolve_capture(request, ["articles"], ["saved", "note"])
    assert receipt.state == "saved" and kind == "article"
    assert media_hub.rows["articles"][url]["note"] is None


def test_gateway_url_resolution_preserves_submitted_identity_without_unrestricted_scraping(
    monkeypatch,
):
    from types import SimpleNamespace
    from core import media_resolution

    monkeypatch.setattr(
        media_resolution, "enrich_context", lambda *args: pytest.fail("unrestricted enrichment")
    )
    monkeypatch.setattr(
        media_resolution,
        "generate_with_retry",
        lambda **kwargs: SimpleNamespace(text='{"Title":"Example","URL":"https://other.test/"}'),
    )
    written = []
    monkeypatch.setattr(
        media_resolution,
        "write_resolved",
        lambda category, data, **kwargs: written.append(data) or SaveReceipt("needs_review", ""),
    )
    for url, category in [
        ("https://example.test/article", "articles"),
        ("https://www.thisamericanlife.org/1/test", "podcasts"),
    ]:
        media_resolution.resolve_capture({**REQUEST, "input": {"url": url}}, [category], ["saved"])
        assert written[-1]["URL"] == url


def test_queued_capture_cannot_reuse_permissions_removed_before_processing():
    store, caller = fixture()
    media_capture.submit_capture(store, caller, REQUEST)
    result = media_capture.process_capture(
        store,
        {**caller, "allowed_fields": ["saved"]},
        REQUEST["request_id"],
        resolver=lambda *args, **kwargs: pytest.fail("removed edit authority"),
    )
    assert result["state"] == "needs_review"


def test_malformed_delegated_fields_fail_as_unauthorized():
    store, caller = fixture()
    with pytest.raises(capture_clients.Unauthorized):
        media_capture.submit_capture(store, {**caller, "allowed_fields": [{}]}, REQUEST)
