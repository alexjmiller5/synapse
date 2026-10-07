"""Regressions from independent review of the consumer service contract."""

import pytest

from core import external_data, media_capture, media_resolution
from core.media_save import SaveReceipt
from test_media_capture import REQUEST, fixture
from test_media_save import existing


@pytest.mark.parametrize("value", [0, False, {}])
def test_inferred_falsy_note_cannot_bypass_saved_only_authority(monkeypatch, media_hub, value):
    url = "https://example.test/article"
    media_hub.rows["articles"] = {url: {**existing(), "id": url}}
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"URL": url, "Notes": value})
    _, result = media_resolution.resolve_capture(
        {**REQUEST, "input": {"url": url}}, ["articles"], ["saved"]
    )
    assert result.state == "needs_review"
    assert media_hub.rows["articles"][url]["note"] == existing()["note"]
    assert not media_hub.writes


def test_submitted_youtube_url_controls_the_handler_identity(monkeypatch):
    submitted = "https://www.youtube.com/watch?v=first-video"
    extracted = "https://www.youtube.com/watch?v=wrong-video"
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"Video URL": extracted})
    observed = []
    monkeypatch.setattr(
        media_resolution,
        "write_resolved",
        lambda category, data, **kwargs: observed.append(data) or SaveReceipt("needs_review", ""),
    )
    media_resolution.resolve_capture(
        {**REQUEST, "input": {"url": submitted}}, ["youtube-videos"], ["saved"]
    )
    assert observed[0]["Video URL"] == submitted


def test_same_uuid_retries_resolver_failure_before_any_primary_write():
    store, caller = fixture()
    media_capture.submit_capture(store, caller, REQUEST)

    def unavailable(*args, **kwargs):
        raise TimeoutError("classifier unavailable")

    assert (
        media_capture.process_capture(store, caller, REQUEST["request_id"], resolver=unavailable)[
            "state"
        ]
        == "uncertain"
    )
    calls = []

    def recovered(*args, **kwargs):
        calls.append(1)
        return "youtubeVideo", SaveReceipt(
            "saved", "first-video", {"updated_at": "r", "hub_at": "r"}
        )

    media_capture.submit_capture(store, caller, REQUEST)
    assert (
        media_capture.process_capture(store, caller, REQUEST["request_id"], resolver=recovered)[
            "state"
        ]
        == "saved"
    )
    assert calls == [1]


@pytest.mark.parametrize("category", ["movies", "tv-shows"])
def test_ambiguous_remakes_never_reach_media_writer(monkeypatch, media_hub, category):
    monkeypatch.setenv("TMDB_API_KEY", "synthetic")
    monkeypatch.setattr(media_resolution, "classify", lambda text: category)
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"Title": "Example"})
    monkeypatch.setattr(
        external_data,
        "tmdb_search",
        lambda *args: [
            {"id": 1, "title": "Example", "year": "2000", "votes": 100},
            {"id": 2, "title": "Example", "year": "2020", "votes": 200},
        ],
    )
    _, result = media_resolution.resolve_capture(REQUEST, [category], ["saved"])
    assert result.state == "needs_review"
    assert not media_hub.writes


def test_article_fragment_preserves_existing_producer_identity_and_status(monkeypatch, media_hub):
    url = "https://www.google.com/chrome/whats-new/archive/#feature-one"
    media_hub.rows["articles"] = {url: {**existing(), "id": url}}
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"Title": "Example", "URL": url})
    _, result = media_resolution.resolve_capture(
        {**REQUEST, "input": {"url": url}}, ["articles"], ["saved"]
    )
    assert result.state == "saved" and result.identity == url
    assert list(media_hub.rows["articles"]) == [url]
    assert media_hub.rows["articles"][url]["saved"] == 1
    assert media_hub.rows["articles"][url]["status"] == "Finished"


def test_unknown_fragment_identity_requires_review_instead_of_collapsing(monkeypatch, media_hub):
    url = "https://example.test/feature/#detail"
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"Title": "Example", "URL": url})
    _, result = media_resolution.resolve_capture(
        {**REQUEST, "input": {"url": url}}, ["articles"], ["saved"]
    )
    assert result.state == "needs_review"
    assert not media_hub.writes


@pytest.mark.parametrize("value", [0, False, {}])
def test_authorized_note_still_requires_a_text_value(monkeypatch, media_hub, value):
    url = "https://example.test/article"
    monkeypatch.setattr(media_resolution, "extract", lambda *args: {"URL": url, "Notes": value})
    _, result = media_resolution.resolve_capture(
        {**REQUEST, "input": {"url": url}}, ["articles"], ["saved", "note"]
    )
    assert result.state == "needs_review"
    assert not media_hub.writes
