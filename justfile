default:
    @just --list

sync:
    uv sync

run-processor-debug: sync
    cd workers/processor && uv run functions-framework --target=processor --source=main.py --debug

run-processor: sync
    cd workers/processor && uv run functions-framework --target=processor --source=main.py

recept-local-batch:
    cd scripts && uv run --with requests send_local_requests.py local_requests.txt

recept +args:
    uv run --with requests scripts/recept.py {{quote(args)}}

test: sync
    cd workers/processor && uv run --group dev pytest tests/ -v --ignore=tests/test_integration.py

test-cov: sync
    cd workers/processor && uv run --group dev pytest tests/ -v --cov --cov-report=term-missing -m "not integration"

test-integration: sync
    cd workers/processor && uv run --group dev pytest tests/test_integration.py -v --timeout=120

reveal-synapse-notion-secret:
  op item get 'SYNAPSE_NOTION_INTERNAL_INTEGRATION_SECRET' --fields credential --reveal
