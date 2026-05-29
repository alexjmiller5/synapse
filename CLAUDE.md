# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Synapse is a serverless middleware that captures natural-language text and routes it to Notion databases. It uses AI (Gemini 2.5 Flash) to parse, classify, and extract structured data from unstructured input.

## Architecture

```
HTTP Request → intaker (Cloud Run) → Pub/Sub → processor (Cloud Run) → Notion API
                                                                     ↳ External APIs (Spotify, YouTube, TMDB, Google Maps)
```

**Two services in a uv workspace monorepo:**
- `services/intaker/` - HTTP endpoint that validates and publishes to Pub/Sub
- `workers/processor/` - Main AI processing worker (handles parsing, classification, extraction, Notion writes)

**The processor pipeline:**
1. Parse raw input (split by `@` delimiter, extract `$` context)
2. Classify intent using Gemini with dynamic project context
3. Extract structured fields based on database schema
4. Execute business logic and write to Notion

## Build & Development Commands

```bash
# Install dependencies
just sync

# Run processor locally with debug logging
just run-processor-debug

# Run processor locally (production mode)
just run-processor

# Run tests (from worker directory)
cd workers/processor && uv run pytest tests/ -v

# Send batch requests from local_requests.txt
just recept-local-batch

# Send single request to deployed API
just recept "your text here"

# Add package to specific service (use workspace member name, not directory)
uv add --package synapse-processor <package-name>
uv add --package synapse-intaker <package-name>
```

**Local testing:** Add the `syn-local` shell function from README.md to send Cloud Event-formatted requests to `localhost:8080`. Tests mock all GCP/external services via `conftest.py` module-level mocks.

## Key Configuration Files

- `config.yaml` - GCP project settings, secret names, email addresses
- `workers/processor/databases.yaml` - All 25+ Notion database schemas with AI extraction rules
- `workers/processor/prompts.yaml` - Gemini system prompts for parsing/classification/extraction

## Configuration-Driven AI Behavior

The processor is entirely YAML-driven. To add a new Notion database category:

1. Add secret `notion-<category>-db-id` to GCP Secret Manager
2. Add category definition to `databases.yaml` with:
   - `description` - Used by AI for classification decisions
   - `helper: true` (optional) - Marks a "helper" DB (e.g. `trips`, `logs`, `youtube-channels`). Helper DBs are NOT classification or extraction targets; they exist only to be *related to* by other categories (e.g. a `places` page linked to a trip). Both the classifier prompt (`ai_engine.generate_classification_prompt`) and option hydration (`business_logic.hydrate_dynamic_options`) skip them via this flag.
   - `properties` - Field mappings with `type`, `required`, `instruction`, `allowlist`, `virtual`, `create_new`

Property field meanings:
- `instruction` - Extraction prompt (supports `{current_date}`, `{raw_text}` placeholders). For `date` fields the instruction MUST require ISO 8601 (`YYYY-MM-DD`) output, since `notion_utils._notion_date` validates the value and raises on empty/non-ISO input (logged as a Bug rather than silently dropped).
- `virtual: true` - Hidden from AI, populated by Python code only
- `allowlist` - Strict enum values for select/multi_select/status
- `create_new: true` - Allows AI to create new values beyond allowlist

## Code Organization (processor/)

- `main.py` - Entry point, orchestrates the pipeline (`run_pipeline` processes each item)
- `config.py` - Loads all YAML configs (`CONFIG`, `DATABASES`, `PROMPTS` globals)
- `schemas.py` - JSON schemas for Gemini structured output (parser, classifier, extractor)
- `ai_engine.py` - Gemini interactions, prompt generation, schema building
- `business_logic.py` - Notion queries, inventory hydration, handler dispatch
- `handlers.py` - Category-specific logic (places, youtube, movies, bookmarks, etc.)
- `notion_utils.py` - Property builders and Notion API operations
- `external_data.py` - URL extraction, web scraping, external API enrichment
- `gcp_secrets.py` - Secret Manager access with caching
- `clients.py` - Singleton client initialization (Gemini, Notion, Spotify, etc.)

## Notion API Access

To query Notion databases locally, use the 1Password CLI to retrieve the API token:

```bash
op item get 'SYNAPSE_NOTION_INTERNAL_INTEGRATION_SECRET' --fields credential --reveal
```

**Key database IDs:**
- Logs (execution tracking): `2b103953a8af803280cec633c91c46c3`

## Infrastructure

- **Terraform** in `infrastructure/` manages GCP resources
- **GitHub Actions** deploys on push to `main` via Workload Identity Federation
- All secrets stored in GCP Secret Manager (27 total, see `config.yaml` for names)

## User Input Syntax

- `@` splits multiple items in one message
- `$` provides context (project name, date, status, category hint)
- Example: `Buy eggs $ groceries @ Update resume $ Career @ https://youtube.com/...`

## Deployment

Push to `main` triggers GitHub Actions (only when `services/`, `workers/`, `pyproject.toml`, `uv.lock`, `config.yaml`, or the deploy workflow change):
1. Runs test job per service (currently placeholder — tests are TODO in CI)
2. Generates `requirements.txt` from `uv.lock` per service via `uv export --package <name>`
3. Copies `config.yaml` and `.python-version` to service directories
4. Deploys to Cloud Run via `google-github-actions/deploy-cloudrun` using Workload Identity Federation

Infrastructure changes deploy separately via `terraform.yaml` workflow.

## Receptor - iOS & macOS Companion App

The Receptor app has been extracted to its own repo: https://github.com/alexjmiller5/receptor
