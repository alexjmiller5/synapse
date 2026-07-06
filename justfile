set shell := ["bash", "-cu"]

default:
    @just --list

# Dev: live-reloading deploy of app.py against real Modal infra
dev:
    uv run modal serve app.py

test:
    uv run pytest -m "not integration"

# All static analysis (read-only, CI-safe)
check:
    uv run ruff check . && uv run ruff format --check .

fmt:
    uv run ruff format . && uv run ruff check --fix .

# Stream logs from the deployed app
logs:
    uv run modal app logs synapse

# Push .env.tpl secrets into the Modal secret store (no plaintext touches disk;
# `modal secret create --from-dotenv` rejects FIFOs, so a stdin script does the create)
sync-secrets:
    op inject -i .env.tpl | uv run scripts/sync_secrets.py synapse

deploy: test sync-secrets
    uv run modal deploy app.py

# --- project-specific recipes below (one-offs live in scripts/, run directly) ---

# Integration suite — real Gemini calls (key injected via op)
test-integration:
    op run --env-file=.env.tpl -- uv run pytest tests/test_integration.py -v --timeout=120

# Send one thought to the deployed webhook
recept +args:
    MODAL_WEBHOOK_URL="${MODAL_WEBHOOK_URL:-$(op read 'op://Personal/Modal Synapse/webhook-url')}" \
    MODAL_PROXY_TOKEN_ID="${MODAL_PROXY_TOKEN_ID:-$(op read 'op://Personal/Modal Synapse/proxy-token-id')}" \
    MODAL_PROXY_TOKEN_SECRET="${MODAL_PROXY_TOKEN_SECRET:-$(op read 'op://Personal/Modal Synapse/proxy-token-secret')}" \
    uv run scripts/recept.py {{quote(args)}}

# Send every line of scripts/local_requests.txt to the deployed webhook
recept-batch:
    MODAL_WEBHOOK_URL="${MODAL_WEBHOOK_URL:-$(op read 'op://Personal/Modal Synapse/webhook-url')}" \
    MODAL_PROXY_TOKEN_ID="${MODAL_PROXY_TOKEN_ID:-$(op read 'op://Personal/Modal Synapse/proxy-token-id')}" \
    MODAL_PROXY_TOKEN_SECRET="${MODAL_PROXY_TOKEN_SECRET:-$(op read 'op://Personal/Modal Synapse/proxy-token-secret')}" \
    uv run scripts/send_local_requests.py scripts/local_requests.txt
