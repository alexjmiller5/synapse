# Apple Notes intake

The installed `notes-intake` client captures ordinary notes from one selected
inbox into Synapse's existing task and agent dispatch flow. Source cleanup is
off by default and requires explicit verified filing. Enable it on one Mac only when devices mirror the
same iCloud Notes account; each installation owns its own journal.

## Install

Add this flake as an input, import `synapse.darwinModules.notes-intake`, and set:

```nix
services.synapse-notes-intake = {
  enable = true;
  user = "local-user";
};
```

The exported module installs the client and a login-session launchd job. The
job runs every 300 seconds and starts disabled until its user configures an
inbox. Its binaries live in the Nix store, never a development checkout.

## Configure and enroll

Sign into Notes through macOS Internet Accounts and allow Notes automation when
the installed client first requests it. `notes-intake folders` lists folder IDs
without reading bodies. Configure the exact inbox ID and exclusions:

```sh
notes-intake configure --inbox-id '<folder-id>' --exclude-id '<protected-note-id>' --enable
```

Repeat `--exclude-id` for every note to protect; configure replaces that list.
Retain the full list when changing settings. Omit `--enable` to pause intake.

The service operator runs `just clients issue "<installation label>"` and gives
the resulting enrollment link to the installation owner. In a native terminal
in the logged-in Mac session, run `notes-intake enroll` and paste that link at
the hidden prompt. The service URL is retained in app state and the dedicated
capture bearer goes into the login Keychain, service `synapse-notes-intake`,
account `capture`. No provider credentials or machine-vault supplier is used.
Reenroll replacement Macs with a newly issued credential. Revoke only that
client through `just clients revoke <client-id>` when retiring it.

If a credential is already stored but endpoint setup was interrupted, use
`notes-intake enroll --capture-url '<service capture URL>'` from the native
login session to finish connecting without minting another credential.

A custom secure credential supplier can set `NOTES_INTAKE_TOKEN_COMMAND` to a
JSON argv; `NOTES_INTAKE_TOKEN` is also accepted. The Darwin module exposes
`credentialCommand`. Each supplier must return this client's own service token.
Do not use an operator token or another device's token.

## What gets captured

Only the configured folder is read. Excluded IDs are checked before content.
Locked, shared, unreadable or attached notes are held. A case-insensitive
standalone `#keep` in the title, or a line containing only `#keep`, also holds
the note. Mentioning the marker within a paragraph does not exempt it.

Notes must be unchanged for fifteen minutes. Plaintext and Notes HTML, including
formatting/checklist representation, are durably retained before sending.
Unchanged content is captured once, even if only the modification date changes.
Edited content has a new capture identity, the previous identity and a diff;
agents must reconcile earlier outcomes and act only on changed intent. Deleting
text does not authorize undoing work. Protected or moved notes are not retried.

## Operate and verify

`notes-intake preview` shows eligible/held counts without saving or uploading.
`notes-intake once` performs one pass. `notes-intake status` lists policy and
capture receipts without note bodies. Default app state is
`$XDG_STATE_HOME/synapse-notes-intake`, normally
`~/.local/state/synapse-notes-intake`; `--state-dir` or
`NOTES_INTAKE_STATE_DIR` selects another location. The launchd module's stateDir
selects the same location for the installed CLI and service.

`journal.sqlite` contains private original snapshots and frozen requests. Keep
it: removing it forgets local deduplication and can recapture old notes. Logs
contain counts and failure classes only. An accepted receipt means Synapse
queued the exact capture ID; check its durable execution/task outcome separately
when verifying rollout. Failures preserve the request for a later scheduled
retry. A lost response uses the same UUID, so the server journal deduplicates it.
## Verified filing and optional source cleanup

HTTP acceptance and agent startup do not prove a note was filed. A separate,
trusted filing operator must verify the extraction and saved task/data references,
then attest that exact capture. Human or future tasks may remain To Do: the
attestation concerns filing, not completion of their real-world actions.

Provision a separate filing-only operator credential through the service operator:

```sh
just clients issue-filing "<operator installation>" <workspace>
```

Enroll it in the operator's supported native session with
`notes-intake operator-enroll`. It uses a distinct Keychain account
`filing-operator`; the scheduled intake continues using its own `capture` account.
`NOTES_FILING_TOKEN` or `NOTES_FILING_TOKEN_COMMAND` supplies the operator's
credential in other authorized invocation contexts. Never substitute the intake
token, a machine credential or provider credentials. Each caller/service pair
gets its own issued credential. The operator token can finalize its workspace's
captures, but cannot submit new captures. The intake token cannot finalize.

After checking the actual filing outcome:

```sh
notes-intake finalize <capture-uuid> --actor '<operator-session-id>' \
  --item 'tasks/<stable-row-id>' --item 'bookmarks/<stable-row-id>'
```

Include only the actual filed references. The serialized service independently
checks the live source task/execution and original full text, source identity and
live target IDs before storing an immutable receipt. The operator attests that
the referenced content is semantically complete; the service does not infer it
from task status. Existing captures without device ownership metadata require
an explicit verified `--source-client <client-id>` binding. Do not infer that
binding from a mutable device label.

Before that binding exists, a legacy capture's receipt request is forbidden.
The client counts it as held without reading its source; authentication and
server failures still count as errors. A held source never qualifies for deletion.

The service's workspace credential must permit the exact source and target
reads, including the standard identity/revision/tombstone columns. Missing grants
or source text retained only as a remote file reference fail closed. They require
a verified narrow grant/readback solution before that capture can qualify;
never fall back to broader credentials or guessed source equality.

On the intake installation, first run:

```sh
notes-intake cleanup-preview
```

This reads only device-bound receipts for accepted revisions. A filed receipt
must match the local UUID and complete payload digest. Only the latest revision
is considered. The current native note must still be unchanged, quiet, in the
selected inbox, unprotected, unlocked, unshared, attachment-free and readable.
The preview lists current candidates and never deletes them.

After reviewing current candidates and verifying the native implementation:

```sh
notes-intake cleanup-configure --enable
```

The scheduled `once` pass then performs the same checks and makes another exact
fresh-snapshot comparison in Notes immediately before deletion. Source deletion
is not an atomic compare-and-delete API: app/user changes can race native calls,
so do not activate until the real native path has been verified for the intended
workflow. The original snapshot and immutable filing receipt remain durable.
Reconfiguring the inbox automatically pauses cleanup. `cleanup-configure`
without `--enable` pauses cleanup alone; intake can continue.

The client commits a deleting intent before the mutation. A lost response,
interruption or unknown result becomes uncertain and is never automatically
replayed; inspect `status` and reconcile it explicitly. A native held result
retains the source. Excluded IDs are checked before source body access, and a
new `#keep`, edit, lock, share, attachment or move prevents cleanup. Source
cleanup never deletes Soma tasks, source snapshots, journals or capture history.

After actually checking an uncertain source outcome, use
`cleanup-reconcile <capture-uuid> --outcome retained --actor '<operator-id>'
--reason '<what was verified>'` to permit a future fully guarded attempt, or
`--outcome deleted` to record a verified deletion. This command changes only
the local outcome record, never Notes. It accepts only deleting/uncertain entries;
do not use it to guess that a timed-out deletion did or did not happen.
