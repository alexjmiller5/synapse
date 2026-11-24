# Apple Notes intake

The installed `notes-intake` client captures ordinary notes from one selected
inbox into Synapse's existing task and agent dispatch flow. It never modifies,
moves or deletes originals. Enable it on one Mac only when devices mirror the
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
There is no source cleanup or automatic-deletion mode.
