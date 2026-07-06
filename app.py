"""Modal deployment shim — ALL infrastructure lives here, as code.

Business logic stays in src/core/ (plain Python, no Modal imports) so the
same package runs in tests or anywhere else. This file only maps that
logic onto Modal: image, secrets, endpoints.
"""

import modal

APP_NAME = "synapse"  # also the Modal secret name (see justfile sync-secrets)

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_sync()  # reads pyproject.toml + uv.lock
    .add_local_python_source("core")
    .add_local_file("src/core/databases.yaml", "/root/core/databases.yaml")
    .add_local_file("src/core/prompts.yaml", "/root/core/prompts.yaml")
)

secrets = [modal.Secret.from_name(APP_NAME)]


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

    return run(payload)


@app.function(image=image, secrets=secrets)
@modal.fastapi_endpoint(method="POST", requires_proxy_auth=True)
def webhook(payload: dict) -> dict:
    """HTTP entrypoint. Callers (Receptor / iPhone Shortcuts) send Modal-Key +
    Modal-Secret headers; unauthorized requests are rejected at Modal's edge, free."""
    from fastapi import HTTPException

    from core.pipeline import payload_error

    error = payload_error(payload)
    if error:
        raise HTTPException(status_code=422, detail=error)

    call = process.spawn({"raw_text": payload["raw_text"]})
    return {"status": "accepted", "call_id": call.object_id}
