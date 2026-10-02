"""App-issued capture tokens: issue, authenticate, revoke, list, enrollment link."""

from urllib.parse import parse_qs, urlsplit

import pytest

from core import capture_clients as cc


def test_issue_returns_a_token_once_and_stores_only_its_hash():
    store = {}
    issued = cc.issue(store, "Alex iPhone")
    assert issued["label"] == "Alex iPhone"
    assert len(issued["token"]) >= 40
    assert issued["token"] not in repr(store)


def test_issued_token_authenticates_to_its_client():
    store = {}
    issued = cc.issue(store, "phone")
    client = cc.authenticate(store, f"Bearer {issued['token']}")
    assert client["client_id"] == issued["client_id"]
    assert client["label"] == "phone"


@pytest.mark.parametrize("header", [None, "", "Bearer", "Basic abc", "Bearer wrong-token"])
def test_missing_or_wrong_credentials_are_unauthorized(header):
    store = {}
    cc.issue(store, "phone")
    with pytest.raises(cc.Unauthorized):
        cc.authenticate(store, header)


def test_revoked_token_stops_authenticating_and_others_keep_working():
    store = {}
    phone = cc.issue(store, "phone")
    laptop = cc.issue(store, "laptop")
    assert cc.revoke(store, phone["client_id"]) is True
    with pytest.raises(cc.Unauthorized):
        cc.authenticate(store, f"Bearer {phone['token']}")
    assert cc.authenticate(store, f"Bearer {laptop['token']}")["label"] == "laptop"


def test_revoking_an_unknown_client_reports_false():
    assert cc.revoke({}, "nope") is False


def test_list_shows_labels_and_state_without_secrets():
    store = {}
    phone = cc.issue(store, "phone")
    cc.issue(store, "laptop")
    cc.revoke(store, phone["client_id"])
    listed = {c["label"]: c for c in cc.list_clients(store)}
    assert set(listed) == {"phone", "laptop"}
    assert listed["phone"]["revoked"] is True and listed["laptop"]["revoked"] is False
    assert all("hash" not in key for c in listed.values() for key in c)


@pytest.mark.parametrize("label", ["", "   ", None, 7, "x" * 81])
def test_issue_rejects_bad_labels(label):
    with pytest.raises(cc.InvalidRequest):
        cc.issue({}, label)


def test_enrollment_link_keeps_the_secret_out_of_the_server_request():
    link = cc.enrollment_link(
        "https://ws--synapse-enroll.modal.run", "https://ws--synapse-capture.modal.run", "tok/en+="
    )
    parts = urlsplit(link)
    assert parts.query == ""  # the token rides in the fragment, which browsers never send
    assert parse_qs(parts.fragment) == {
        "url": ["https://ws--synapse-capture.modal.run"],
        "token": ["tok/en+="],
    }
