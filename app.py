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
    .add_local_file("store.py", remote_path="/root/store.py")
)

secrets = [modal.Secret.from_name(APP_NAME)]

# raw_text hash -> epoch seconds last processed; the pipeline skips exact resends
# inside its dedup window. Entries expire after 7 idle days on Modal's side.
seen_inputs = modal.Dict.from_name(f"{APP_NAME}-seen-inputs", create_if_missing=True)

# Durable state (store.VolumeStore over this Volume): workspaces (core.workspace:
# each user's overlay, property ids and Notion / life-data credentials) and
# per-device capture tokens (core.capture_clients: hashes only, each bound to a
# workspace). The operator edits both with scripts/workspace.py and
# scripts/capture_clients.py.
state = modal.Volume.from_name(f"{APP_NAME}-state", create_if_missing=True)  # = store.STATE_VOLUME


def _state():
    from store import VolumeStore

    return VolumeStore(state)


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
    """Background worker — .spawn()ed from the webhook. spawn() IS the queue.
    Runs inside the capture's workspace: its config, ids and credentials."""
    from core import workspace
    from core.pipeline import run

    store = _state()
    with workspace.use(workspace.load(store, payload.get("workspace") or workspace.DEFAULT_ID)):
        media = payload.get("media_operation")
        if media:
            from core import media_capture

            if media["action"] == "submit":
                return media_capture.submit_capture(store, media["caller"], media["request"])
            if media["action"] == "process":
                return media_capture.process_capture(store, media["caller"], media["request_id"])
            raise ValueError("unsupported media operation")
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

    call = process.spawn(
        {
            "raw_text": payload["raw_text"],
            "source": payload.get("source"),
            "workspace": payload.get("workspace") or "default",
        }
    )
    return {"status": "accepted", "call_id": call.object_id}


@app.function(image=image)
@modal.fastapi_endpoint(method="POST", label=f"{APP_NAME}-capture")
def capture(payload: dict, authorization: Annotated[str | None, Header()] = None):
    """Client entrypoint (Receptor): `Authorization: Bearer <app-issued token>`."""
    from fastapi.responses import JSONResponse

    from core import capture_clients
    from core.pipeline import payload_error

    try:
        client = capture_clients.authenticate(_state(), authorization)
        if client.get("purpose"):
            raise capture_clients.Unauthorized()
    except capture_clients.Unauthorized:
        return JSONResponse(
            {"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    error = payload_error(payload)
    if error:
        return JSONResponse({"error": error}, status_code=422)
    call = process.spawn(
        {
            "raw_text": payload["raw_text"],
            "source": payload.get("source"),
            "workspace": client["workspace"],  # from the token, never the body
        }
    )
    return {"status": "accepted", "call_id": call.object_id}


@app.function(image=image)
@modal.fastapi_endpoint(method="POST", label=f"{APP_NAME}-media-capture")
def media_capture_endpoint(payload: dict, authorization: Annotated[str | None, Header()] = None):
    """Dedicated gateway credential; all durable changes use the single worker."""
    from fastapi.responses import JSONResponse
    from core import capture_clients, media_capture

    try:
        media_capture.validate_envelope(payload)
        caller = media_capture.gateway_caller(_state(), authorization, payload)
        operation = {"action": payload["action"], "caller": caller}
        if payload["action"] == "submit":
            operation["request"] = payload["request"]
        else:
            return media_capture.get_capture(_state(), caller, payload["request_id"])
        receipt = process.remote({"workspace": caller["workspace"], "media_operation": operation})
        if payload["action"] == "submit" and receipt["state"] in (
            "received",
            "processing",
            "uncertain",
        ):
            process.spawn(
                {
                    "workspace": caller["workspace"],
                    "media_operation": {
                        "action": "process",
                        "caller": caller,
                        "request_id": receipt["request_id"],
                    },
                }
            )
        return receipt
    except capture_clients.Unauthorized:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    except media_capture.InvalidRequest:
        return JSONResponse({"error": "invalid capture"}, status_code=400)
    except media_capture.Conflict:
        return JSONResponse({"error": "request conflict"}, status_code=409)
    except media_capture.NotFound:
        return JSONResponse({"error": "capture not found"}, status_code=404)


@app.function(image=image)
@modal.fastapi_endpoint(method="GET", label=f"{APP_NAME}-enroll")
def enroll():
    """Static page an enrollment link opens; it forwards to receptor://enroll."""
    from fastapi.responses import HTMLResponse

    from core.enroll_page import ENROLL_PAGE

    return HTMLResponse(ENROLL_PAGE, headers={"Cache-Control": "no-store"})
