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
