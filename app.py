"""Modal deployment shim — ALL infrastructure lives here, as code.

Business logic stays in src/core/ (plain Python, no Modal imports) so the
same package runs in tests or anywhere else. This file only maps that
logic onto Modal: image, secrets, endpoints.
"""

from typing import Annotated

import modal
from fastapi import Header

APP_NAME = "synapse"  # also the Modal secret name (see justfile sync-secrets)

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_sync(extra_options="--no-dev")  # reads pyproject.toml + uv.lock; skip dev group
    # add_local_python_source("core") can't resolve src/core (the package is never
    # installed and src/ is only on sys.path under pytest) — mount the dir instead.
    # Lands at /root/core, importable in-container, yaml files included for free.
    .add_local_dir("src/core", remote_path="/root/core", ignore=["**/__pycache__"])
)

secrets = [modal.Secret.from_name(APP_NAME)]

# raw_text hash -> epoch seconds last processed; the pipeline skips exact resends
# inside its dedup window. Entries expire after 7 idle days on Modal's side.
seen_inputs = modal.Dict.from_name(f"{APP_NAME}-seen-inputs", create_if_missing=True)

# Per-device capture tokens (core.capture_clients): hashes only. Issued and
# revoked by the operator with scripts/capture_clients.py.
capture_clients_store = modal.Dict.from_name(f"{APP_NAME}-capture-clients", create_if_missing=True)


@app.function(
    image=image,
    secrets=secrets,
    timeout=600,
    memory=512,
    # max_containers=1 preserves the old Cloud Run max_instances=1 serialization —
    # Notion dedupe is query-then-create, not atomic.
    max_containers=1,
    retries=modal.Retries(max_retries=3, backoff_coefficient=2.0),
)
def process(payload: dict):
    """Background worker — .spawn()ed from the webhook. spawn() IS the queue."""
    from core.pipeline import run

    return run(payload, seen=seen_inputs)


@app.function(image=image, secrets=secrets)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
def webhook(payload: dict) -> dict:
    """Operator entrypoint (`just recept`, agents): Modal-Key + Modal-Secret
    headers; unauthorized requests are rejected at Modal's edge, free. Client
    apps use `capture` with their own token instead."""
    from fastapi import HTTPException

    from core.pipeline import payload_error

    error = payload_error(payload)
    if error:
        raise HTTPException(status_code=422, detail=error)

    call = process.spawn({"raw_text": payload["raw_text"], "source": payload.get("source")})
    return {"status": "accepted", "call_id": call.object_id}


@app.function(image=image)
@modal.fastapi_endpoint(method="POST", label=f"{APP_NAME}-capture")
def capture(payload: dict, authorization: Annotated[str | None, Header()] = None):
    """Client entrypoint (Receptor): `Authorization: Bearer <app-issued token>`."""
    from fastapi.responses import JSONResponse

    from core import capture_clients
    from core.pipeline import payload_error

    try:
        capture_clients.authenticate(capture_clients_store, authorization)
    except capture_clients.Unauthorized:
        return JSONResponse(
            {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    error = payload_error(payload)
    if error:
        return JSONResponse({"error": error}, status_code=422)
    call = process.spawn({"raw_text": payload["raw_text"], "source": payload.get("source")})
    return {"status": "accepted", "call_id": call.object_id}


@app.function(image=image)
@modal.fastapi_endpoint(method="GET", label=f"{APP_NAME}-enroll")
def enroll():
    """Static page an enrollment link opens; it forwards to receptor://enroll."""
    from fastapi.responses import HTMLResponse

    from core.enroll_page import ENROLL_PAGE

    return HTMLResponse(ENROLL_PAGE, headers={"Cache-Control": "no-store"})
