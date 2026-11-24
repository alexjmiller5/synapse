# Verified Notes Cleanup Implementation Plan

For execution: use `superpowers:executing-plans` inline; no additional workers.

Goal: remove an unchanged eligible source note only after durable capture and explicit operator-verified filing.

Architecture: the serialized service owns device-bound filing receipts. The native client reads only its own receipts with its existing capture credential, then rechecks the exact source before deletion. Cleanup defaults off; uncertain mutations never repeat automatically.

Tech stack: existing Python service, standard-library native client, SQLite and the existing JXA bridge. No new runtime dependencies.

Spec: [Notes intake and verified cleanup](../../notes-intake.md).

## Constraints

- Preserve the current capture endpoint, UUID deduplication and media gateway.
- Never infer filing completion from HTTP acceptance, task In Progress or human task completion.
- Exclude protected IDs before body access; retain kept, locked, shared, attached, changed and unreadable notes.
- Keep original snapshots and cleanup results durable; no provider credentials in clients.
- No native test, installation/rebuild or live deletion without resource admission and a concrete current eligibility preview.

## Steps

- [x] Add device ownership and read-only receipt requests to the existing client endpoint, tested with synthetic clients and the real endpoint functions.
- [x] Add serialized operator finalization: verify the full original task/execution source and live filed target references before retaining an immutable receipt. Provide a separately enrolled filing-only role and installed operator CLI; existing captures require explicit device binding.
- [x] Add default-off native cleanup planning and durable deleting/deleted/uncertain states. Test revisions, exclusions, incomplete proofs, lost responses and restart behavior before implementation.
- [x] Add a guarded native delete action using the same pre-body exclusions and a fresh full snapshot comparison; exercise it with synthetic native objects only.
- [x] Wire installed CLI preview/configuration/status and scheduled cleanup, document receipt meanings, actor responsibilities and explicit uncertain-outcome reconciliation, then run the offline/static suite and mutation checks.
- [ ] Commit/push and verify CI. Preserve the completed initial rollout. Defer native verification, install and activation until admitted; review current candidates before actual deletion.

## Review focus

- Older captures require an explicitly verified device binding; a guessed client label is insufficient.
- Missing narrow source/target read permissions and archived-only source fields must retain originals.
- Operator attestation is distinct from independent source/target existence checks and from completion of human tasks.
- Native source mutation is not atomic with a user edit; no synthetic test substitutes for native verification.
- Lost or no-op deletion responses must become uncertain rather than success or automatic retry.
