set shell := ["bash", "-cu"]
# Keep the venv out of the iCloud-synced checkout: iCloud stalls reads of
# venv files and hides editable-install .pth files.
export UV_PROJECT_ENVIRONMENT := env_var("HOME") + "/.cache/uv-venvs/synapse"

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

# Classifier prompt eval — real Gemini calls against scripts/eval_cases.yaml
eval-classifier ws="default":
    MODAL_TOKEN_ID=op://4eeyrkqibibn7k4j6rz2fbzvxm/2sfxybjpv3c3ohzxhf5qeken4a/token_id MODAL_TOKEN_SECRET=op://4eeyrkqibibn7k4j6rz2fbzvxm/2sfxybjpv3c3ohzxhf5qeken4a/token_secret SYNAPSE_WORKSPACE={{ws}} op run --env-file=.env.tpl -- uv run scripts/eval_classifier.py

# Integration suite — real Gemini calls (key injected via op)
test-integration:
    op run --env-file=.env.tpl -- uv run pytest tests/test_integration.py -v --timeout=120

# Send one thought to the deployed webhook
recept +args:
    MODAL_WEBHOOK_URL="${MODAL_WEBHOOK_URL:-$(op read 'op://skkfhuuqegdpyzuobf6h6dyoly/tf26sufzmjt3zphrx37hexmbse/webhook-url')}" \
    MODAL_PROXY_TOKEN_ID="${MODAL_PROXY_TOKEN_ID:-$(op read 'op://skkfhuuqegdpyzuobf6h6dyoly/tf26sufzmjt3zphrx37hexmbse/proxy-token-id')}" \
    MODAL_PROXY_TOKEN_SECRET="${MODAL_PROXY_TOKEN_SECRET:-$(op read 'op://skkfhuuqegdpyzuobf6h6dyoly/tf26sufzmjt3zphrx37hexmbse/proxy-token-secret')}" \
    uv run scripts/recept.py {{quote(args)}}

# Per-device capture tokens: `just clients issue "<device>" [workspace]` prints
# an enrollment link; also `list` and `revoke <client_id>`. Operator Modal auth.
clients action arg="" ws="":
    MODAL_TOKEN_ID=op://4eeyrkqibibn7k4j6rz2fbzvxm/2sfxybjpv3c3ohzxhf5qeken4a/token_id MODAL_TOKEN_SECRET=op://4eeyrkqibibn7k4j6rz2fbzvxm/2sfxybjpv3c3ohzxhf5qeken4a/token_secret op run --no-masking -- uv run scripts/capture_clients.py {{action}} {{quote(arg)}} {{ws}}

# Workspaces (one user's config overlay and Soma hub credentials):
# `list`, `show <id>`, `pull <id> <dir>`, `push <id> <dir>`, `set-secrets <id>` (stdin)
workspace action *args:
    MODAL_TOKEN_ID=op://4eeyrkqibibn7k4j6rz2fbzvxm/2sfxybjpv3c3ohzxhf5qeken4a/token_id MODAL_TOKEN_SECRET=op://4eeyrkqibibn7k4j6rz2fbzvxm/2sfxybjpv3c3ohzxhf5qeken4a/token_secret op run --no-masking -- uv run scripts/workspace.py {{action}} {{args}}
