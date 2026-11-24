"""Capture behavior at the native-reader / durable-journal / HTTP boundaries."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import shutil
import sys

import pytest


NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


def note(**changes):
    return {
        "id": "note-1",
        "folder_id": "inbox-1",
        "title": "An idea",
        "plaintext": "An idea\nBuild a useful thing",
        "html": "<div>An idea</div><div>Build a useful thing</div>",
        "modified_at": "2026-01-02T11:00:00Z",
        "created_at": "2026-01-02T10:00:00Z",
        "locked": False,
        "shared": False,
        "attachments": [],
        **changes,
    }


def policy(**changes):
    return {
        "enabled": True,
        "inbox_id": "inbox-1",
        "exclude_ids": ["protected-1"],
        "quiet_seconds": 900,
        **changes,
    }


def test_capture_persists_full_source_before_send_and_dedupes_restart(tmp_path):
    from notes_intake.main import Journal, run_once

    sent = []

    def upload(payload):
        with Journal(tmp_path) as observer:
            stored = observer.rows()
        assert len(stored) == 1
        assert json.loads(stored[0]["snapshot"]) == note()
        assert stored[0]["status"] == "pending"
        assert stored[0]["capture_id"] == payload["capture_id"]
        sent.append(payload)
        return {"status": "accepted", "capture_id": payload["capture_id"], "call_id": "call-1"}

    with Journal(tmp_path) as journal:
        result = run_once(policy(), [note()], journal, upload, now=NOW)
    assert result["accepted"] == 1
    assert "Build a useful thing" in sent[0]["raw_text"]
    assert "note-1" in sent[0]["raw_text"]
    assert sent[0]["source"] == "apple-notes"
    with Journal(tmp_path) as journal:
        assert run_once(policy(), [note()], journal, upload, now=NOW)["accepted"] == 0
        assert journal.rows()[0]["status"] == "accepted"
    assert len(sent) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "protected-1"},
        {"folder_id": "other"},
        {"locked": True},
        {"shared": True},
        {"shared": None},
        {"attachments": ["attachment-1"]},
        {"title": "Ideas #KeEp"},
        {"plaintext": "Ideas\n  #KEEP  \nPrivate draft"},
        {"modified_at": "2026-01-02T11:59:00Z"},
        {"modified_at": "invalid"},
        {"error": "unreadable"},
        {"locked": None},
    ],
)
def test_exclusions_never_persist_or_send_content(tmp_path, changes):
    from notes_intake.main import Journal, run_once

    def forbidden(_):
        pytest.fail("excluded content left the device")

    with Journal(tmp_path) as journal:
        run_once(policy(), [note(**changes)], journal, forbidden, now=NOW)
        assert journal.rows() == []


def test_mentioning_keep_in_prose_does_not_exempt_note(tmp_path):
    from notes_intake.main import Journal, run_once

    sent = []
    with Journal(tmp_path) as journal:
        run_once(
            policy(),
            [note(plaintext="Explain the #keep marker")],
            journal,
            lambda p: sent.append(p) or {"status": "accepted", "capture_id": p["capture_id"]},
            now=NOW,
        )
    assert len(sent) == 1


def test_lost_response_reuses_frozen_capture_and_exemption_prevents_retry(tmp_path):
    from notes_intake.main import Journal, run_once

    attempts = []

    def lost(payload):
        attempts.append(deepcopy(payload))
        raise TimeoutError("response lost")

    with Journal(tmp_path) as journal:
        assert run_once(policy(), [note()], journal, lost, now=NOW)["failed"] == 1
    with Journal(tmp_path) as journal:
        run_once(policy(), [note()], journal, lost, now=NOW)
        run_once(policy(), [note(title="An idea #keep")], journal, lost, now=NOW)
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]


def test_changed_note_includes_prior_capture_and_revert_is_a_new_edit(tmp_path):
    from notes_intake.main import Journal, run_once

    sent = []

    def upload(p):
        sent.append(deepcopy(p))
        return {"status": "accepted", "capture_id": p["capture_id"]}

    changed = note(
        plaintext="An idea\nBuild a useful thing\nAdd a second thing",
        html="<div>Updated content</div>",
        modified_at="2026-01-02T11:30:00Z",
    )
    with Journal(tmp_path) as journal:
        run_once(policy(), [note()], journal, upload, now=NOW)
        run_once(policy(), [changed], journal, upload, now=NOW)
        run_once(policy(), [note()], journal, upload, now=NOW)
        assert len(journal.rows()) == 3
    assert len(sent) == 3
    assert sent[0]["capture_id"] != sent[1]["capture_id"]
    assert sent[0]["capture_id"] in sent[1]["raw_text"]
    assert "+Add a second thing" in sent[1]["raw_text"]
    assert "reconcile" in sent[1]["raw_text"].lower()
    assert sent[1]["capture_id"] in sent[2]["raw_text"]
    assert sent[0]["capture_id"] != sent[2]["capture_id"]
    assert "-Add a second thing" in sent[2]["raw_text"]


def test_upload_of_first_note_cannot_send_second_after_it_becomes_protected(tmp_path):
    from notes_intake.main import Journal, run_once

    current = {"note-1": note(), "note-2": note(id="note-2")}
    sent = []

    def upload(p):
        sent.append(p)
        current["note-2"] = note(id="note-2", title="Private #keep")
        return {"status": "accepted", "capture_id": p["capture_id"]}

    with Journal(tmp_path) as journal:
        result = run_once(
            policy(),
            list(current.values()),
            journal,
            upload,
            now=NOW,
            refresh=lambda n: current[n["id"]],
        )
        assert len(journal.rows()) == 1
    assert len(sent) == 1
    assert result["held"] == 1


def test_pending_edit_reverted_after_successor_never_replays_original_request(tmp_path):
    from notes_intake.main import Journal, run_once

    attempts = []

    def upload(p):
        attempts.append(p)
        if len(attempts) == 1:
            raise TimeoutError("never reached server")
        return {"status": "accepted", "capture_id": p["capture_id"]}

    with Journal(tmp_path) as journal:
        run_once(policy(), [note()], journal, upload, now=NOW)
        run_once(
            policy(),
            [note(plaintext="Changed intent", html="<div>Changed intent</div>")],
            journal,
            upload,
            now=NOW,
        )
        run_once(policy(), [note()], journal, upload, now=NOW)
    assert len(attempts) == 3
    assert attempts[2]["capture_id"] != attempts[0]["capture_id"]
    assert attempts[1]["capture_id"] in attempts[2]["raw_text"]
    assert "This is an EDIT" in attempts[2]["raw_text"]


def test_invalid_receipt_does_not_mark_accepted_and_disabled_means_no_content(tmp_path):
    from notes_intake.main import Journal, run_once

    with Journal(tmp_path) as journal:
        result = run_once(policy(enabled=False), [note()], journal, lambda _: {}, now=NOW)
        assert result["accepted"] == 0
        assert journal.rows() == []
        result = run_once(policy(), [note()], journal, lambda _: {"status": "accepted"}, now=NOW)
        assert result["failed"] == 1
        assert journal.rows()[0]["status"] == "pending"


def test_http_boundary_sends_frozen_capture_and_refuses_redirects(monkeypatch, tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from notes_intake import main

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, data, self.headers["Authorization"]))
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps({"status": "accepted", "capture_id": data["capture_id"]}).encode()
            )

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(main, "valid_url", lambda url: url)  # only the loopback fixture uses HTTP
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with main.Journal(tmp_path) as journal:
            result = main.run_once(
                policy(),
                [note()],
                journal,
                lambda p: main.upload(base + "/capture", "test-token", p),
                now=NOW,
            )
            assert result["accepted"] == 1
            assert received[0][2] == "Bearer test-token"
            assert json.loads(journal.rows()[0]["payload"]) == received[0][1]
        with pytest.raises(Exception):
            main.upload(base + "/redirect", "test-token", received[0][1])
        assert [entry[0] for entry in received] == ["/capture", "/redirect"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid",
        "https://user:secret@example.invalid",
        "https://example.invalid?token=x",
    ],
)
def test_unsafe_capture_urls_are_refused(url):
    from notes_intake.main import valid_url

    with pytest.raises(ValueError):
        valid_url(url)


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Keychain CLI contract")
def test_keychain_interactive_command_finishes_successfully_at_eof():
    from notes_intake.main import keychain_command

    # help is read-only; this catches an invalid trailing command after a successful write.
    keychain_command(["help"])


def test_reconnect_preserves_policy_and_requires_existing_credential(monkeypatch):
    from notes_intake import main

    config = policy()
    monkeypatch.setattr(main, "token", lambda: "existing-client-token")
    main.enroll(config, "https://capture.example.invalid")
    assert config == {**policy(), "capture_url": "https://capture.example.invalid"}

    def missing():
        raise ValueError("not enrolled")

    monkeypatch.setattr(main, "token", missing)
    with pytest.raises(ValueError):
        main.enroll(config, "https://other.example.invalid")
    assert config["capture_url"] == "https://capture.example.invalid"


def test_native_reader_excludes_before_body_and_holds_races_and_attachments():
    script = Path(__file__).parents[1] / "src/notes_intake/notes.jxa"
    harness = r"""
    const vm = require('node:vm');
    const fs = require('node:fs');
    const bodyReads = [];
    const old = new Date('2020-01-01');
    const make = (id, options={}) => ({
      id: () => id, modificationDate: () => old,
      creationDate: () => old, name: () => options.title || 'A capture',
      passwordProtected: () => options.locked || false,
      shared: () => options.shared || false,
      attachments: {id: () => options.attachments || []},
      plaintext: () => { bodyReads.push(id); return options.text || 'A capture\nDo a thing'; },
      body: () => { if(options.race) options.shared = true; return '<div>Full source</div>'; }
    });
    const notes = [make('excluded'), make('kept-title', {title:'A #KeEp note'}),
      make('locked', {locked:true}), make('shared', {shared:true}),
      make('attached', {attachments:['att']}), make('kept-body', {text:'Draft\n#KEEP\nPrivate'}),
      make('racing', {race:true}), make('good')];
    const collection = () => notes;
    collection.id = () => notes.map(n=>n.id());
    const folder = {id:()=> 'inbox-1', shared:()=>false, notes:collection};
    const context = {Application:()=>({folders:()=>[folder]}), Date};
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
    const result = JSON.parse(context.run(['read', JSON.stringify({inbox_id:'inbox-1',
      exclude_ids:['excluded'], quiet_seconds:900})]));
    console.log(JSON.stringify({result, bodyReads}));
    """
    # The native JXA adapter is plain JavaScript; CI's Node also supplies vm.
    runtime = shutil.which("bun") or shutil.which("node")
    assert runtime, "Native-adapter tests require Bun or Node"
    result = subprocess.run(
        [runtime, "-e", harness, str(script)], capture_output=True, text=True, check=True
    )
    data = json.loads(result.stdout)
    assert data["bodyReads"] == ["kept-body", "racing", "good"]
    assert [n["id"] for n in data["result"]["notes"]] == ["good"]
    assert data["result"]["held"] == 7
