# AGENTS.md

Synapse: AI middleware that captures natural-language text and routes it to
life-data (most categories) or to Notion (tasks, plus the Executions log).
Python service deployed on Modal: HTTP webhook + spawned background worker. No cron in this app.

## Architecture rule (the one that matters)

**Business logic lives in `src/core/` as plain Python with NO Modal imports.**
Only `app.py` imports `modal` — it is the deployment shim (image, secrets,
endpoints). This keeps the logic portable: the same `core` package runs in
tests or on any future platform.

- Webhook → worker handoff is `process.spawn(payload)` — Modal's spawn IS the
  queue. Do not add Pub/Sub/Redis/celery.
- `process` runs with `max_containers=1`: Notion dedupe is query-then-create,
  not atomic, so runs must be serialized. Don't raise it.
- Exact resends are dropped server-side: `run(payload, seen=...)` hashes the
  stripped `raw_text` and skips it when the same hash was processed inside
  `pipeline.DEDUP_WINDOW_S` (24h). The store is the `synapse-seen-inputs`
  `modal.Dict` (app.py); the key is written only AFTER a run completes, so a
  crashed run still gets its Modal retry. Receptor's iOS background uploads
  re-send when a success callback is lost - this is the backstop for that.
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

Payload: `{"raw_text": str, "source": str | null}`. `source` is a free-form
caller label naming the surface that captured it (Receptor's table is in its
AGENTS.md: `ios-app`, `macos-panel`, `share-send`, `app-shortcut`,
`hammerspoon-hyper-r`, ...; plus `agent`, `cli`) logged as the execution's
`Source` select (new labels auto-create options); Synapse never parses it. A standalone `pj` token in text or context forces a project task
(`PJ_KEYWORD`): category tasks, project linked (contains-match, else one
classifier call), token stripped from the task name.

1. `parse_raw_input` — Gemini splits `@`-separated items, pulls `$` context;
   skipped entirely (verbatim pass-through) when the text has no `@`/`$` —
   the LLM round-trip has mangled URLs it was meant to copy
2. Classify → category (+ optional `related_project` / `project_action`)
3. Extract structured fields per the workspace's category schema
4. `apply_business_logic` + category handler → Notion writes (or a life-data
   row push); every outcome logged to the Notion Logs DB; failures create a
   High-priority task

Everything is YAML-driven: `src/core/template/databases.yaml` (schemas,
generic allowlists, per-field extraction instructions) and
`src/core/template/prompts.yaml` (system prompts), each run's copy merged with
the active workspace's overlay (below).

## Workspaces (no personal config in the repo)

The repo ships only the product. One user's Notion ids, allowlists and wording,
place tags, Notion property-id map, and Notion + life-data credentials are a
**workspace** (`core/workspace.py`), stored in the app's `synapse-state` Volume
(`store.VolumeStore`, one JSON file per key; a modal.Dict is NOT used because
its entries expire after 7 idle days) and edited with `just workspace ...`.

- A workspace's config = template deep-merged with its overlay (dicts merge,
  lists and scalars replace). The overlay may only touch categories and
  properties the template has. `tasks.place_tags` join the Tags allowlist.
- `core.config.DATABASES` / `PROMPTS` / `PROPERTY_IDS` are live views of
  `workspace.current()`; the worker wraps each capture in
  `workspace.use(workspace.load(...))`, so the pipeline code is workspace-blind.
- Device tokens (`core/capture_clients.py`) carry their workspace; the capture
  endpoint takes it from the token, never the body. The operator webhook takes
  an optional `workspace` field (default `default`). Dedup is per workspace.
- App credentials (Gemini, TMDB, Spotify, YouTube) stay in `.env.tpl`; a
  workspace's Notion token and life-data hub are workspace secrets
  (`just workspace set-secrets <id>`, KEY=VALUE on stdin). Rotating one =
  re-run set-secrets from its 1Password item.
- Without an active workspace (tests, local scripts) `current()` is the local
  one: overlay + property ids from `$SYNAPSE_WORKSPACE_DIR`, credentials from
  env. Tests use `tests/fixtures/workspace` (fake ids). Local tools take
  `SYNAPSE_WORKSPACE=<id>` (the just recipes set it) to run in a stored one.
- **Onboarding someone**: `just workspace push <id> <dir>` (their overlay),
  `just workspace set-secrets <id>` (their Notion + hub), `just sync-prop-ids
  <id>`, then `just clients issue "<device>" <id>` and send the link.
Both files are `add_local_file`d into the image at `/root/core/`.

## Conventions

- Secrets are env vars ONLY (Modal secret `synapse` in the cloud, `op run`
  locally). `.env.tpl` is the canonical manifest (op:// refs, committed).
  `core/settings.py` is the typed surface: a pydantic-settings `Settings`
  (field `gemini_api_key` ← env `GEMINI_API_KEY`) read via the lru_cached
  `get_settings()`. **Only ever call `get_settings()` inside a function, never
  at module import** — Modal injects secrets at container start, so an
  import-time read caches stale `None`s (this bit TMDB once). External clients
  follow the same rule: `core/clients.py` exposes lazy `get_notion()`,
  `get_gemini_client()`, etc. (lru_cached, built on first use, `None` if the
  key is absent) — nothing is instantiated at import. `core/secrets.py`'s
  `core/secrets.get_db_id` is the one Notion DB-id lookup (active workspace).
- **Most categories are life-data tables, not Notion DBs.** A stanza with
  `hub_table` is one: `core/life_hub.py: push_rows` POSTs `{table, columns,
  rows}` to the hub's `/v1/rows/push` with the `LIFE_HUB_URL` /
  `LIFE_HUB_TOKEN` settings (a `tables:read,tables:write` token: the handlers
  also pull rows). Push ONLY the columns you know - the hub's upsert touches
  exactly the columns sent, so a status capture never blanks tags. The
  CATALOG enforces what this yaml used to (required fields, option
  vocabularies, uniqueness, defaults); a rejected row files a cleanup task
  and writes nothing. Two handler shapes:
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
  A `hub_table` stanza carries no `db_id` and is skipped by
  `hydrate_dynamic_options`, `validate_all`, and
  `scripts/fetch_property_ids.py`, so its yaml allowlists ARE the prompt's
  options - keep them in step with life-data's catalog (`life property list
  <table>`). The grocery inventory the extraction prompt sees comes from the
  hub too (`fetch_inventory_map`). `Created Item` on the Executions log holds
  `<table>/<id>`, not a URL - it is a Notion url property, so `log_job_outcome`
  retries once without it (ref moved into `AI Summary`) rather than lose the
  whole row. A handler that wrote nothing returns `handlers.Failed(detail)`,
  which the pipeline logs as `Error(s)`; returning None there would log a
  Success over an empty result.
- Notion DB ids are workspace data: a category stanza's `db_id` and the
  top-level `db_ids` mapping (logs, trips, projects, notes) in the workspace
  overlay, read through `get_db_id`. The template carries none.
- Notion **properties** are written/hydrated by their stable **id**, not name
  (rename-safe). `databases.yaml` keeps human names (the AI needs them); the
  name→id map is the workspace's `property_ids` (generated by
  `scripts/fetch_property_ids.py` — `just sync-prop-ids [workspace]`). `build_notion_properties`
  stays name-keyed; `keys_to_ids`/`prop_id` translate at the write boundary
  (`create_page`, `update_status`, relation writes, `log_job_outcome`) and
  hydration matches by id. Re-run the generator after ADDING a property (a
  rename alone keeps working). Option VALUES (select/status/multi_select) stay
  by NAME — new options auto-create by name and Notion's select-write is
  name-first. Read-side query filters/sorts still reference names (fail-safe).
- All "today"/date creation goes through `core/timeutils.py`
  (`today_eastern()` / `now_eastern()`) — never `date.today()` /
  `datetime.now()` (server is UTC; late-night captures would date-shift).
  life-data timestamps are the exception and use `now_utc_iso_ms()`: sync
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
  (e.g. podcasts `Podcast Name` / `Producer`) are `create_new: true` so they
  never enum at all.

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
| `just workspace list\|show\|pull\|push\|set-secrets` | Workspaces: a user's overlay, property ids, credentials |
| `just validate [ws]` / `just sync-prop-ids [ws]` | Drift check / property-id refresh against a stored workspace's Notion |

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
`tests/conftest.py` seeds fake secrets as env vars and swaps all external
clients (`core.clients` globals) for MagicMocks — no test touches the network.

## Receptor

The iOS/macOS companion app lives at https://github.com/alexjmiller5/receptor.
It POSTs `{"raw_text": ..., "source": ...}` to `capture` with its own bearer
token (from an enrollment link) and expects 200.

## Media capture writes

Resolved movie, TV, video, article and podcast captures use `core/media_save.py` through the
existing hub client. New identities use insert-only creation; existing rows
receive only requested fields through revision-checked patches. Missing status
is an initializer default only and never resets a stored status. Tombstones,
conflicts, rejection and uncertain transport remain explicit non-success states.
No save path falls back to row push. Channel discovery no longer initializes
external subscription tracking and preserves existing source rows.

`Capture Intent` separates explicit saves from consumption reports. Per-category
`capture_columns` and optional `saved_column` are workspace configuration.
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

Life Data is an approved consumer of Synapse's media-capture endpoint. Its
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
