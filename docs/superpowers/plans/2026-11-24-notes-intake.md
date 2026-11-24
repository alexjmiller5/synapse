# Notes Intake Implementation Plan

Approved behavior: inbox-only native Notes capture, five-minute checks after
fifteen quiet minutes, case-insensitive standalone `#keep`, explicit excluded
IDs, locked/shared/unreadable/attachment holds, durable retry deduplication,
revision context, and retained originals. The owner's selections are runtime
state, never source literals. Implementation proceeds inline under the existing
approval.

1. Add failing end-to-end tests using a temporary SQLite journal, a local HTTP
   capture service and synthetic Notes snapshots. Prove first capture, restart
   after lost HTTP response, exclusions, quiet interval, changed revisions,
   refusal handling and unchanged-note deduplication.
2. Implement `src/notes_intake/`: a conservative native JXA reader, a local
   durable journal and a CLI with configure/status/once/enroll commands. Use
   Synapse's existing client capture endpoint and one separately issued client
   token. Keep snapshots private and never delete Notes content. Test the
   native adapter against synthetic application objects as well as a native
   metadata-only dry run.
3. Package the client in a Synapse flake and exported Darwin module. Document
   enrollment, privacy boundaries, receipt semantics and reenrollment. Run
   the full existing suite and checks, then mutation checks for new guards.
4. Commit and push; verify cloud CI. Pin the package in nix-config, proof-build
   and switch the mini. Configure actual inbox/exclusions through the installed
   CLI and enroll its own credential in Keychain. Verify actual captures reach
   durable tasks and unchanged reruns create no duplicates. The laptop remains
   disabled to avoid double intake from two iCloud replicas.

Review focus: disappearing or changing notes, an exemption added before retry,
shared state changes during reads, uncertain HTTP acceptance, and revisions
that reintroduce old content. Fail closed on unreadable native metadata and
retain full snapshots on transport or payload failure.
