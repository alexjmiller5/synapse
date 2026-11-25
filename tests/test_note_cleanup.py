"""Verified filing and exact-source safety at the cleanup mutation boundary."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from notes_intake import main
from test_notes_intake import NOW, note, policy


def accepted(journal, source=None):
    row = journal.prepare(source or note())
    journal.accept(row["capture_id"], {"status": "accepted", "capture_id": row["capture_id"]})
    return journal.rows()[-1]


def proof(row, **changes):
    return {
        "state": "filed",
        "capture_id": row["capture_id"],
        "actor": "fixture-session",
        "source_digest": hashlib.sha256(
            json.loads(row["payload"])["raw_text"].encode()
        ).hexdigest(),
        "items": [{"table": "tasks", "id": "filed-task"}],
        **changes,
    }


def test_cleanup_defaults_off_and_preview_never_mutates(tmp_path):
    with main.Journal(tmp_path) as journal:
        row = accepted(journal)

        def forbidden(*args):
            pytest.fail("cleanup mutation was not enabled")

        assert (
            main.cleanup_once(policy(), journal, forbidden, forbidden, forbidden, now=NOW)[
                "deleted"
            ]
            == 0
        )
        result = main.cleanup_once(
            policy(),
            journal,
            lambda _: note(),
            lambda _: proof(row),
            forbidden,
            preview=True,
            now=NOW,
        )
        assert result["eligible"] == 1
        assert result["deleted"] == 0


def test_matching_filing_and_source_delete_once_with_durable_intent(tmp_path):
    calls = []
    with main.Journal(tmp_path) as journal:
        row = accepted(journal)

        def remove(expected):
            state = journal.db.execute(
                "SELECT state FROM cleanup WHERE capture_id=?", (row["capture_id"],)
            ).fetchone()[0]
            assert state == "deleting"
            assert expected == note()
            calls.append(expected["id"])
            return {"state": "deleted", "note_id": expected["id"]}

        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: note(),
            lambda _: proof(row),
            remove,
            now=NOW,
        )
        assert result["deleted"] == 1
    with main.Journal(tmp_path) as journal:
        main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: note(),
            lambda _: proof(row),
            remove,
            now=NOW,
        )
    assert calls == ["note-1"]


@pytest.mark.parametrize(
    "changes",
    [
        {"plaintext": "Edited text"},
        {"html": "<div>Edited formatting</div>"},
        {"title": "New title"},
        {"title": "#keep"},
        {"locked": True},
        {"shared": True},
        {"attachments": ["attachment"]},
        {"error": "unreadable"},
        {"folder_id": "elsewhere"},
        {"id": "different-note"},
    ],
)
def test_source_changes_or_protections_prevent_delete(tmp_path, changes):
    with main.Journal(tmp_path) as journal:
        row = accepted(journal)
        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: note(**changes),
            lambda _: proof(row),
            lambda _: pytest.fail("unsafe delete"),
            now=NOW,
        )
        assert result["deleted"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"state": "captured"},
        {"state": "received"},
        {"capture_id": "other"},
        {"source_digest": "wrong"},
        {"items": []},
        {"actor": ""},
    ],
)
def test_incomplete_or_mismatched_proof_prevents_source_access(tmp_path, changes):
    with main.Journal(tmp_path) as journal:
        row = accepted(journal)
        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: pytest.fail("invalid proof read source"),
            lambda _: proof(row, **changes),
            lambda _: pytest.fail("invalid proof deleted"),
            now=NOW,
        )
        assert result["deleted"] == 0


def test_protected_id_excluded_before_receipt_or_source_read(tmp_path):
    with main.Journal(tmp_path) as journal:
        accepted(journal, note(id="protected-1"))

        def forbidden(*args):
            pytest.fail("protected source accessed")

        result = main.cleanup_once(
            policy(cleanup_enabled=True), journal, forbidden, forbidden, forbidden, now=NOW
        )
        assert result["deleted"] == 0


def test_lost_delete_response_is_uncertain_and_never_replayed(tmp_path):
    calls = []
    with main.Journal(tmp_path) as journal:
        row = accepted(journal)

        def remove(expected):
            calls.append(expected["id"])
            raise TimeoutError("Deleted but acknowledgement lost")

        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: note(),
            lambda _: proof(row),
            remove,
            now=NOW,
        )
        assert result["uncertain"] == 1
    with main.Journal(tmp_path) as journal:
        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: note(),
            lambda _: proof(row),
            remove,
            now=NOW,
        )
        assert result["uncertain"] == 1
    assert calls == ["note-1"]


@pytest.mark.parametrize("status", [401, 403, 500])
def test_unavailable_receipts_never_read_sources_and_only_unbound_ones_are_held(tmp_path, status):
    from urllib.error import HTTPError

    with main.Journal(tmp_path) as journal:
        accepted(journal)

        def unavailable(_):
            raise HTTPError("https://capture.invalid", status, "Unavailable", {}, None)

        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: pytest.fail("Unverified source was read"),
            unavailable,
            lambda _: pytest.fail("Unverified source was deleted"),
            now=NOW,
        )
    assert result["deleted"] == 0
    assert result["held"] == (1 if status == 403 else 0)
    assert result["failed"] == (0 if status == 403 else 1)


def test_old_revision_receipt_cannot_delete_new_current_revision(tmp_path):
    with main.Journal(tmp_path) as journal:
        old = accepted(journal)
        accepted(journal, note(plaintext="New revision"))
        result = main.cleanup_once(
            policy(cleanup_enabled=True),
            journal,
            lambda _: pytest.fail("new revision not filed"),
            lambda _: proof(old),
            lambda _: pytest.fail("old delete"),
            now=NOW,
        )
        assert result["deleted"] == 0


def test_native_delete_checks_exact_snapshot_exclusions_and_propagates_uncertainty():
    script = Path(__file__).parents[1] / "src/notes_intake/notes.jxa"
    harness = r"""
    const vm = require('node:vm'), fs = require('node:fs');
    let text='Original', reads=0, deletes=0, uncertain=false, present=true, noOp=false;
    const old=new Date('2020-01-01');
    const n={id:()=> 'note-1', name:()=> 'Title', plaintext:()=>{reads++;return text},
      body:()=> '<div>Original</div>', modificationDate:()=>old, creationDate:()=>old,
      passwordProtected:()=>false, shared:()=>false, attachments:{id:()=>[]}};
    const notes=()=>present?[n]:[]; notes.id=()=>present?['note-1']:[];
    const folder={id:()=> 'inbox-1',shared:()=>false,notes};
    const context={Date,Application:()=>({folders:()=>[folder],delete:()=>{
      deletes++; if(!noOp) present=false; if(uncertain) throw Error('reply lost');
    }})};
    vm.createContext(context); vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
    const policy={inbox_id:'inbox-1',exclude_ids:[],quiet_seconds:900,note_id:'note-1'};
    policy.expected=JSON.parse(context.run(['read',JSON.stringify(policy)])).notes[0];
    text='Changed';
    const changed=JSON.parse(context.run(['delete',JSON.stringify(policy)]));
    if(deletes || changed.state!=='held') throw Error('changed source deleted');
    text='Original';policy.exclude_ids=['note-1'];const before=reads;
    const excluded=JSON.parse(context.run(['delete',JSON.stringify(policy)]));
    if(deletes || reads!==before || excluded.state!=='held') throw Error('exclusion bypassed');
    policy.exclude_ids=[];
    const good=JSON.parse(context.run(['delete',JSON.stringify(policy)]));
    if(deletes!==1 || good.state!=='deleted') throw Error('matching source not deleted');
    present=true;uncertain=true;let propagated=false;
    try {context.run(['delete',JSON.stringify(policy)])}catch(e){propagated=true}
    if(!propagated || deletes!==2) throw Error('uncertain deletion reported safe');
    present=true;uncertain=false;noOp=true;propagated=false;
    try {context.run(['delete',JSON.stringify(policy)])}catch(e){propagated=true}
    if(!propagated) throw Error('no-op deletion reported successful');
    console.log('guarded');
    """
    runtime = shutil.which("bun") or shutil.which("node")
    result = subprocess.run([runtime, "-e", harness, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "guarded"


def test_installed_finalize_cli_uses_separate_operator_credential(tmp_path, monkeypatch):
    main.save_config(
        tmp_path / "config.json",
        {"enabled": False, "operator_url": "https://capture.example.invalid"},
    )
    monkeypatch.setenv("NOTES_FILING_TOKEN", "operator-token")
    monkeypatch.setenv("NOTES_INTAKE_TOKEN", "capture-token")
    capture_id = "45601840-1df2-47e5-9eb3-9175ed2932e3"
    seen = []

    def post(url, token, payload):
        assert url == "https://capture.example.invalid"
        assert token == "operator-token"
        assert payload == {
            "action": "finalize",
            "request": {
                "capture_id": capture_id,
                "actor": "fixture-session",
                "items": [{"table": "tasks", "id": "target"}],
            },
        }
        seen.append(payload)
        return {"state": "filed", "capture_id": capture_id}

    monkeypatch.setattr(main, "upload", post)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notes-intake",
            "--state-dir",
            str(tmp_path),
            "finalize",
            capture_id,
            "--actor",
            "fixture-session",
            "--item",
            "tasks/target",
        ],
    )
    assert main.cli() == 0
    assert len(seen) == 1


def test_uncertain_state_requires_explicit_cli_reconciliation(tmp_path, monkeypatch):
    with main.Journal(tmp_path) as journal:
        row = accepted(journal)
        with journal.db:
            journal.db.execute(
                "INSERT INTO cleanup VALUES(?,?,?)",
                (row["capture_id"], "uncertain", json.dumps(proof(row))),
            )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notes-intake",
            "--state-dir",
            str(tmp_path),
            "cleanup-reconcile",
            row["capture_id"],
            "--outcome",
            "retained",
            "--actor",
            "operator",
            "--reason",
            "Verified source still present",
        ],
    )
    assert main.cli() == 0
    with main.Journal(tmp_path) as journal:
        state, saved = journal.db.execute("SELECT state,proof FROM cleanup").fetchone()
        assert state == "retained"
        assert json.loads(saved)["reconciliation"]["reason"] == "Verified source still present"


def test_scheduled_pass_reports_cleanup_transport_failure_without_deleting(tmp_path, monkeypatch):
    main.save_config(
        tmp_path / "config.json",
        policy(cleanup_enabled=True, capture_url="https://capture.example.invalid"),
    )
    with main.Journal(tmp_path) as journal:
        accepted(journal)

    def native(action, config):
        assert action == "read"
        return {"notes": [note()], "held": 0}

    def unavailable(*args):
        raise ConnectionError("receipt service unavailable")

    monkeypatch.setattr(main, "native", native)
    monkeypatch.setattr(main, "token", lambda: "capture-token")
    monkeypatch.setattr(main, "upload", unavailable)
    monkeypatch.setattr(sys, "argv", ["notes-intake", "--state-dir", str(tmp_path), "once"])
    assert main.cli() == 1
    with main.Journal(tmp_path) as journal:
        assert journal.rows()[0]["status"] == "accepted"
