# AGENTS.md

Synapse: AI middleware that captures natural-language text and files it as
Soma rows: a task or a category row per item, plus one execution log row.
Python service deployed on Modal: HTTP webhook + spawned background worker. No cron in this app.

## Architecture rule (the one that matters)

**Business logic lives in `src/core/` as plain Python with NO Modal imports.**
Only `app.py` imports `modal` — it is the deployment shim (image, secrets,
endpoints). This keeps the logic portable: the same `core` package runs in
tests or on any future platform.

- Webhook → worker handoff is `process.spawn(payload)` — Modal's spawn IS the
  queue. Do not add Pub/Sub/Redis/celery.
- `process` runs with `max_containers=1`: one serialized worker owns the
  capture journals and media receipts in the operational store, and
  name/url dedupe (groceries, bookmarks) is query-then-write, not atomic.
  Don't raise it.
- Resends are idempotent by `capture_id`: every capture carries a client
  UUID, and `run(payload, store)` journals it (`core.workflow`), so a
  Receptor upload re-sent after a lost success callback replays the frozen
  result instead of writing twice.
- Authenticated entrypoints:
  - `webhook` (`requires_proxy_auth=True`): the OPERATOR path (`just recept`,
    agents) - `Modal-Key` + `Modal-Secret` headers, workspace credentials.
  - `capture` (label `synapse-capture`): the CLIENT path (Receptor) -
    `Authorization: Bearer <token>`, a per-device token Synapse itself issues
    (`core/capture_clients.py`, hashes only, in the `synapse-state`
    Volume through `store.VolumeStore`). A client app never holds workspace credentials.
  - `media_capture_endpoint` (label `synapse-media-capture`): the delegated
    service path, restricted to media categories and fields by its dedicated token.
  - `just clients issue "<device>"` mints a token and prints an enrollment
    link to the `enroll` page (label `synapse-enroll`); the token rides in
    the URL fragment, so it never reaches a server, and the page hands it to
    `receptor://enroll`. `just clients list` / `revoke <client_id>` manage
    them; revoking one device touches no other.

## The pipeline (`core/pipeline.py: run`)

Payload: `{"raw_text": str, "source": str | null, "capture_id": uuid}`.
`source` is a free-form caller label naming the surface that captured it
(Receptor's table is in its AGENTS.md: `ios-app`, `macos-panel`,
`share-send`, `app-shortcut`, `hammerspoon-hyper-r`, ...; plus `agent`,
`cli`) logged as the execution's free-text `source`; Synapse never parses it. A standalone `pj` token in text or context forces a project task
(`PJ_KEYWORD`): category tasks, project linked (contains-match, else one
classifier call), token stripped from the task name.

1. `parse_raw_input` — Gemini splits `@`-separated items, pulls `$` context;
   skipped entirely (verbatim pass-through) when the text has no `@`/`$` —
   the LLM round-trip has mangled URLs it was meant to copy
2. Classify → category (+ optional `related_project`)
3. Extract structured fields: the prompt and JSON schema are built from the
   category's `prompts.yaml` stanza over its table's Soma catalog (below)
4. `apply_business_logic` + category handler → Soma rows; every outcome is
   an execution row; a failed preparation files a High-priority task

## The catalog enforces, Synapse interprets

Soma's catalog owns each column's contract: type, required, select options
and their `d` meanings, defaults, enforced invariants. `core/catalog.py`
fetches `GET /v1/catalog`, keeps the slice for the tables the workspace config
names, and caches it in the operational store (`catalog:<workspace>`): reread
after `TTL_S` (1 h) with ETag revalidation, dropped when the hub rejects a
write (a `rejected` list, or HTTP 400/422 - the contract may have changed),
served stale when the hub is unreachable. `ai_engine.fields(category)` joins
it with `src/core/template/prompts.yaml`, which holds only interpretation:
classifier descriptions and precedence, each category's `table` / `columns`
/ `capture_columns` wiring and per-field `instruction`s. A field `allowlist`
narrows the catalog's options to the values Synapse may choose (values the
catalog lacks are dropped with a warning) and its instruction then owns their
meaning - tasks need one because their catalog options are the preserved
historical vocabulary; `required: true` makes the extractor always answer a
field the catalog leaves optional. Capture fields are never forced and get no
default, so a neutral mention never resets a stored status. The prompt lists
un-narrowed options with their meanings, the catalog default of fields the
row needs, and the table's enforced invariant texts. Never copy a type,
option list or default into the yaml: change the catalog (`soma property
set`).

## Workspaces (no personal config in the repo)

The repo ships only the product. One user's workflow table bindings, wording
and allowlist overrides, place tags and Soma hub credentials are a
**workspace** (`core/workspace.py`), stored in the app's `synapse-state` Volume
(`store.VolumeStore`, one JSON file per key; a modal.Dict is NOT used because
its entries expire after 7 idle days) and edited with `just workspace ...`.

- A workspace's config = `prompts.yaml` deep-merged with its overlay (dicts
  merge, lists and scalars replace). The overlay may only touch categories
  and properties the template has (under `categories`), plus its `workflow`
  bindings. `categories.tasks.place_tags` join the Tags allowlist.
- `core.config.PROMPTS` / `CATEGORIES` are live views of
  `workspace.current()`; the worker wraps each capture in
  `workspace.use(workspace.load(store, ...))`, so the pipeline code is
  workspace-blind and the catalog cache lands in that store.
- Device tokens (`core/capture_clients.py`) carry their workspace; the capture
  endpoint takes it from the token, never the body. The operator webhook takes
  an optional `workspace` field (default `default`). Capture journals are
  per workspace.
- App credentials (Gemini, TMDB, Spotify, YouTube) stay in `.env.tpl`; a
  workspace's Soma hub URL and token are workspace secrets
  (`just workspace set-secrets <id>`, KEY=VALUE on stdin). Rotating one =
  re-run set-secrets from its 1Password item. The `default` workspace's hub
  token is its own enrollment with the Soma profile `synapse-workspace-v1`
  (broad `tables:read`/`tables:write` plus read/write `raw/synapse-executions/`;
  copy in the Synapse ENV field `SOMA_HUB_TOKEN`).
- Without an active workspace (tests, local scripts) `current()` is the local
  one: the overlay from `$SYNAPSE_WORKSPACE_DIR`, credentials from env.
  Tests use `tests/fixtures/workspace` (workflow bindings) and
  `tests/fixtures/catalog.json` (a generic catalog). Local tools take
  `SYNAPSE_WORKSPACE=<id>` (the just recipes set it) to run in a stored one.
- **Onboarding someone**: `just workspace push <id> <dir>` (their overlay
  with `workflow.tasks` / `executions` / `projects` bindings), `just
  workspace set-secrets <id>` (their hub), then `just clients issue
  "<device>" <id>` and send the link.
`src/core` (with `template/prompts.yaml`) is mounted into the image at
`/root/core/`.

## Conventions

- Secrets are env vars ONLY (Modal secret `synapse` in the cloud, `op run`
  locally). `.env.tpl` is the canonical manifest (op:// refs, committed).
  `core/settings.py` is the typed surface: a pydantic-settings `Settings`
  (field `gemini_api_key` ← env `GEMINI_API_KEY`) read via the lru_cached
  `get_settings()`. **Only ever call `get_settings()` inside a function, never
  at module import** — Modal injects secrets at container start, so an
  import-time read caches stale `None`s (this bit TMDB once). External clients
  follow the same rule: `core/clients.py` exposes lazy
  `get_gemini_client()`, `get_spotify()`, `get_youtube()` (lru_cached, built
  on first use, `None` if the key is absent) — nothing is instantiated at
  import.
- **Every category is a Soma table.** A stanza's `table` names it:
  `core/soma_hub.py: push_rows` POSTs `{table, columns, rows}` to the hub's
  `/v1/rows/push` with the workspace's `SOMA_HUB_URL` /
  `SOMA_HUB_TOKEN` settings (broad `tables:read,tables:write`: the handlers
  also pull rows, and the hub refuses table-scoped writes to `groceries`,
  `ideas`, `movies` and `tv_shows`). Push ONLY the columns you know - the hub's upsert touches
  exactly the columns sent, so a status capture never blanks tags.
  `pull_rows` exhausts bounded pages and fails on missing/repeated cursors; a
  failed scan never returns a partial inventory. Workflow adapters use
  `insert_rows` for stable-ID creation (preserves existing/tombstoned rows and
  verifies exhaustive receipts; stamps the `updated_at` the hub requires on
  every inserted row, and names each rejection's column and rule in
  `InsertRejected`) and `patch_row` for revision-guarded edits. Test fakes
  of the hub (`tests/media_hub.py`) enforce that insert contract: a fake that
  accepts what the real hub rejects hides a 100% production failure.
  Neither helper retries ambiguous writes or falls back to upsert. The
  catalog enforces the contract; a rejected row files a cleanup task and
  writes nothing. Two handler shapes:
  - `handle_hub_logic` (groceries, ideas, fun-activities, bucket-list,
    podcasts, bookmarks): driven entirely by the stanza - `columns` maps
    extracted property names to catalog columns, `constants` adds fixed
    columns (`things_to_do.kind`), `match_on` names the natural key (a
    grocery by `name`, a bookmark by `url`) so a repeat capture updates the
    existing row instead of duplicating it, `review_if_missing` turns an
    unfillable property into a `needs_review` reason when the matched row
    lacks it too (a later capture that fills it clears that reason). A
    bookmark whose page could not be fetched is pushed without a guessed
    Title, never as a cleanup task, and its guessed Description/Tags go in
    `data["_fill_only"]`: they fill empty columns but never replace a known
    url's values. New rows get a random 32-hex id.
  - Resolved-id handlers (movies, tv-shows, youtube-videos): the row id is an
    external id (TMDB, YouTube) that `external_data` resolves first - no
    confident match = a cleanup task and no write, because a wrong id
    silently merges two films. Everything else on those rows (title, year,
    genres, cast, poster) is DERIVED on the hub from the id; sending a
    guessed value gets the row rejected for missing provenance. A YouTube
    channel is pushed once, gated by `known_channel_ids()` against the hub's
    actual state, with a "Classify new Channel" cleanup task.
  The grocery inventory the extraction prompt sees comes from the hub too
  (`fetch_inventory_map`). An execution's `Created Item` holds
  `<table>/<id>`. A handler that wrote nothing returns
  `handlers.Failed(detail)` (with a cleanup task when the user must finish
  it), which the pipeline logs as `Error(s)`; returning None there would log
  a Success over an empty result, and raising inside the journaled write
  step would make Modal retry a deterministic failure until the capture is
  lost.
- `workflow.projects` in a workspace overlay selects Soma project reads:
  `table`, `title_column`, `status_column`, and `active_statuses` are runtime
  values. Omission means no project linking; malformed selected configuration
  fails closed. Duplicate active titles are rejected because the prompt-to-ID
  map cannot represent them safely.
- `workflow.tasks.calendar` optionally sets `timeZone` and `dayStartMinutes`
  (integer 0..1439, absent boundary means midnight). Task extraction and generated
  followups use that civil day. The capture journal freezes it before extraction
  so retries cannot move a task into a later day. Other categories keep their
  own calendar behavior; invalid selected calendars fail closed.
- Oversized execution fields can use runtime `retained_fields`,
  `max_inline_bytes` and `files_prefix`. Retain the frozen original with a
  conditional file create, then byte-verify readback before inserting its row
  reference. The workspace credential has only the configured file prefix;
  no provider storage credentials belong in workspace configuration.
- `core.workflow.WorkflowWriter` journals a mapped output before its checked
  insert, keyed by workspace, persisted capture identity, and item/role path.
  One serialized worker owns its operational store. Retries reuse the original
  table, ID, and body, and never overwrite an existing user row. Capture
  acceptance requires a canonical UUID `capture_id`. Clients persist that ID
  for the submission and reuse it for HTTP retries; identical text with a new
  ID is a new capture. The worker
  freezes its bindings, project/inventory context, parsed items, prepared item
  data, and successful result before marking the capture complete. Changed
  input under the same workspace/capture ID is rejected. Failures propagate to
  Modal retries without creating a second error task over an ambiguous write.
  A workspace without `workflow.tasks` and `workflow.executions` bindings is
  refused before any side effect. Mappings use `table`, `columns`, and
  optional missing-value `defaults`, all runtime state. Execution text/JSON is
  preserved untruncated.
- All "today"/date creation goes through `core/timeutils.py`
  (`today_eastern()` / `now_eastern()`) — never `date.today()` /
  `datetime.now()` (server is UTC; late-night captures would date-shift).
  soma timestamps are the exception and use `now_utc_iso_ms()`: sync
  ordering there is a lexicographic string compare, so UTC with milliseconds
  and a trailing `Z` is load-bearing.
- Gemini calls go through `core.ai_engine.generate_with_retry` (tenacity on
  5xx/bad JSON, one-shot fallback to `GEMINI_FALLBACK_MODEL` on 404). The
  model name lives in ONE place: `core.ai_engine.GEMINI_MODEL`
  (env-overridable).
- Response-schema enums are capped at `ai_engine.MAX_ENUM_OPTIONS` (100):
  Gemini 400s (INVALID_ARGUMENT) when an enum of distinct real-world names
  compiles to too large a constrained-decoding grammar (~150+). Past the cap
  a field silently loses its enum + prompt options dump. Open-world fields
  (e.g. podcasts `Podcast Name` / `Producer`) are catalog `text` columns, so
  they never enum at all.

## Commands

The justfile is the interface, not a script catalog; one-offs go in
`scripts/` and run directly.

| Command | Purpose |
|---|---|
| `just dev` | Live-reload dev against real Modal infra (`modal serve`) |
| `just test` / `just check` / `just fmt` | pytest (unit) / ruff read-only / ruff fix |
| `just test-integration` | Real-Gemini suite (key via 1Password) |
| `just eval-classifier` | Classifier-prompt eval vs `scripts/eval_cases.yaml` (real Gemini) |
| `just logs` | Stream deployed-app logs |
| `just sync-secrets` | Push `.env.tpl` → Modal secret store |
| `just deploy` | test + sync-secrets + `modal deploy` — CI's job, not yours (below) |
| `just recept "text"` | POST one thought to the deployed webhook |
| `just clients issue "<device>" [workspace]` / `list` / `revoke <id>` | Per-device capture tokens; `issue` prints the enrollment link |
| `just workspace list\|show\|pull\|push\|set-secrets` | Workspaces: a user's overlay and hub credentials |

**Deploying = commit + push to `main`.** `.github/workflows/deploy.yml` runs
tests, syncs secrets, and `modal deploy`s — never run `just deploy` locally
unless there's a legitimate stated reason (e.g. CI itself is broken): local
deploys ship code that isn't in git, and the next push silently reverts it.
After pushing, verify with the gh CLI (`gh run watch <id> --exit-status`;
on failure `gh run view <id> --log-failed`) — never assume it succeeded.

## TDD

Write the test in `tests/` first, then the `src/core/` code. `app.py` shim
functions stay thin enough to not need tests (webhook validation is the pure
`core.pipeline.payload_error`, tested in `tests/test_webhook.py`).
`tests/conftest.py` seeds fake secrets as env vars, serves
`tests/fixtures/catalog.json` as the hub's catalog and swaps all external
clients (`core.clients` globals) for MagicMocks — no test touches the network.
`tests/media_hub.py`'s `SyntheticHub` stands in for the hub's row routes;
end-to-end tests run captures through the real journal and assert on its rows.

## Receptor

The iOS/macOS companion app lives at https://github.com/alexjmiller5/receptor.
It POSTs `{"raw_text": ..., "source": ..., "capture_id": ...}` to `capture`
with its own bearer token (from an enrollment link) and expects 200.

## Media capture writes

Resolved movie, TV, video, article and podcast captures use `core/media_save.py` through the
existing hub client. New identities use insert-only creation; existing rows
receive only requested fields through revision-checked patches. Missing status
is an initializer default only and never resets a stored status. Tombstones,
conflicts, rejection and uncertain transport remain explicit non-success states.
No save path falls back to row push. Channel discovery no longer initializes
external subscription tracking and preserves existing source rows.

`Capture Intent` separates explicit saves from consumption reports. Per-category
`capture_columns` and optional `saved_column` are template configuration a
workspace overlay may set.
Configure saved_column only after its cataloged field exists; explicit saves
without that mapping require review. Consumer/provider credentials remain outside
the capture result and never enter native apps.

## Gemini isolation

Use a dedicated Google Cloud project and API key restricted to
`generativelanguage.googleapis.com`. Request quota is per project, not per
key; billing account credit or spending controls may still be shared.
A credential cutover requires a successful `generateContent` request with
the configured model before deployment. Never reuse another app's key.

## Media gateway contract

Soma is an approved consumer of Synapse's media-capture endpoint. Its
stateless gateway holds an independently minted, workspace-bound media-gateway
credential. It delegates opaque caller subjects and an approved logical field
set; a device bearer is never forwarded. Gateway credentials cannot invoke the
general Receptor endpoint. Revocation affects this caller only. Replacing the
gateway client identity also changes its receipt namespace; settle pending jobs
before replacement or retain their private operator reconciliation record.

`core/media_capture.py` stores idempotent requests and receipts in the existing
owned `synapse-state` VolumeStore. All writes and resolution run through the
existing serialized `process` worker; read-only receipt retrieval can run while
it is busy. HTTP acceptance is received, never saved. A saved receipt requires a
committed identity result. Interrupted/uncertain jobs retain their pre-write
resolution plan and reconcile by supported hub reads without replaying edits.

`core/media_resolution.py` permits only configured media categories and fields.
It never enters general task/note fan-out or creates cleanup tasks. Input cannot
choose its workspace, endpoint or credential. Article IDs use the poller's URL
normalization; podcast capture preserves legacy URL-matched IDs and tombstones.
Legacy podcast URL matching is capped at 50 pages of 200 projected rows; larger
or ambiguous catalogs require review. Actual category bindings remain workspace
state. `scripts/media-capture.py` mints the dedicated gateway credential through
the existing operator interface; its one-time output belongs in the caller's
secret store, never in a native app or source control.

The media gateway enriches YouTube through its provider API. Article/podcast
URLs are captured from the submitted input without arbitrary page fetching;
model extraction cannot change a submitted URL. This path never calls general
podcast cleanup-task enrichment. Stored titles remain editable metadata.

Gateway resolution persists a `resolving` phase before provider work and a
`writing` phase at the primary-item checkpoint. Same-request retries may repeat
resolution and insert-once source initialization, but never a primary user-field
write with an uncertain outcome. Old receipts without phase evidence stay
uncertain. Strict movie/TV capture requires a unique exact title match and keeps
an explicit year. Inferred and explicit edits use the same presence semantics
and type checks. Article capture first checks exact existing identity, including
tombstones and fragment IDs; a new fragment identity requires review.
