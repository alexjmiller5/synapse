# AGENTS.md

Synapse: AI middleware that captures natural-language text and routes it to Notion
(most categories) or to life-data (movies, tv-shows).
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
- Endpoints use `requires_proxy_auth=True` — callers send `Modal-Key` +
  `Modal-Secret` headers (mint tokens in the Modal dashboard → Settings →
  Proxy Auth Tokens). Never expose an unauthenticated endpoint.

## The pipeline (`core/pipeline.py: run`)

1. `parse_raw_input` — Gemini splits `@`-separated items, pulls `$` context;
   skipped entirely (verbatim pass-through) when the text has no `@`/`$` —
   the LLM round-trip has mangled URLs it was meant to copy
2. Classify → category (+ optional `related_project` / `project_action`)
3. Extract structured fields per `databases.yaml` schema
4. `apply_business_logic` + category handler → Notion writes (or a life-data
   row push); every outcome logged to the Notion Logs DB; failures create a
   High-priority task

Everything is YAML-driven: `src/core/databases.yaml` (schemas, allowlists,
per-field extraction instructions) and `src/core/prompts.yaml` (system prompts).
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
  `get_secret`/`get_db_id` remain only for the yaml/env DB-id lookup.
- **Not every category is a Notion DB.** A stanza with `hub_table` (movies,
  tv-shows) is a life-data table instead: `core/life_hub.py: push_rows` POSTs
  `{table, columns, rows}` to the hub's `/v1/rows/push` with the `LIFE_HUB_URL`
  / `LIFE_HUB_TOKEN` settings, and the row id is the TMDB id resolved by
  `external_data.resolve_tmdb_id` (no confident match = a cleanup task and no
  write, because a wrong id silently merges two films). Push ONLY the columns
  you know - the hub's upsert touches exactly the columns sent, so a status
  capture never blanks tags. Everything else on those rows (title, year,
  genres, director, cast, poster) is DERIVED on the hub from the id; sending a
  guessed value gets the row rejected for missing provenance. A `hub_table`
  stanza carries no `db_id` and is skipped by `hydrate_dynamic_options`,
  `validate_all`, and `scripts/fetch_property_ids.py`, so its yaml allowlists
  ARE the catalog's options - keep them in step with life-data's catalog.
  `Created Item` on the Executions log holds `<table>/<id>`, not a URL - it is
  a Notion url property, so `log_job_outcome` retries once without it (ref moved
  into `AI Summary`) rather than lose the whole row. A handler that wrote
  nothing returns `handlers.Failed(detail)`, which the pipeline logs as
  `Error(s)`; returning None there would log a Success over an empty result.
- Notion DB ids are committed config, NOT secrets: each Notion-backed category
  stanza in `databases.yaml` has a `db_id` (non-category ids in the top-level `db_ids`
  mapping). `get_db_id` lets a `NOTION_<X>_DB_ID` env var override. Adding a
  DB = one `databases.yaml` edit. (Where ids live may change — e.g. native
  pydantic config — but `get_db_id` stays the single lookup point.)
- Notion **properties** are written/hydrated by their stable **id**, not name
  (rename-safe). `databases.yaml` keeps human names (the AI needs them); the
  name→id map lives in `src/core/property_ids.yaml` (generated by
  `scripts/fetch_property_ids.py` — `just sync-prop-ids`). `build_notion_properties`
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
It POSTs `{"raw_text": ...}` with Modal proxy-auth headers and expects 200.
