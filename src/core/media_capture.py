"""Media-only requests and receipts. Call only through the serialized worker.

State uses the existing durable store. It is not a second queue or scheduler.
"""

import json
import re
from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from core import capture_clients, life_hub
from core.media_save import same_value


class InvalidRequest(ValueError):
    pass


class Conflict(ValueError):
    pass


class NotFound(KeyError):
    pass


def _client(store, caller):
    client = store.get(f"client:{caller.get('client_id')}")
    subject, fields = caller.get("subject"), caller.get("allowed_fields")
    if (
        not client
        or client.get("revoked")
        or client.get("purpose") != "media-gateway"
        or caller.get("workspace") != client.get("workspace")
        or not isinstance(subject, str)
        or not re.fullmatch(r"[a-f0-9]{64}", subject)
        or not isinstance(fields, list)
        or not all(isinstance(field, str) for field in fields)
        or len(set(fields)) != len(fields)
        or not set(fields).issubset(client.get("fields", []))
    ):
        raise capture_clients.Unauthorized()
    return client


def _key(caller, request_id):
    try:
        if str(UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InvalidRequest("invalid request id") from None
    identity = json.dumps(
        [caller["client_id"], caller["subject"], request_id], separators=(",", ":")
    )
    return "media-capture:" + sha256(identity.encode()).hexdigest()


def _request(request, fields):
    if (
        not isinstance(request, dict)
        or set(request) - {"request_id", "input", "intent", "fields"}
        or request.get("intent") not in ("save", "record_consumption")
        or not isinstance(request.get("input"), dict)
        or len(request["input"]) != 1
        or set(request["input"]) - {"text", "url"}
        or any(not isinstance(v, str) or not v.strip() for v in request["input"].values())
        or not isinstance(request.get("fields", {}), dict)
        or not set(request.get("fields", {})).issubset(fields)
        or (request["intent"] == "save" and "saved" not in fields)
    ):
        raise InvalidRequest("invalid capture request")
    try:
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise InvalidRequest("invalid capture request") from None
    if len(encoded.encode()) > 65536:
        raise InvalidRequest("capture too large")
    return encoded


def _receipt(job):
    return {
        "request_id": job["request"]["request_id"],
        "state": job["state"],
        **({"item": deepcopy(job["item"])} if job.get("item") and job["state"] == "saved" else {}),
    }


def submit_capture(store, caller, request):
    _client(store, caller)
    encoded = _request(request, caller["allowed_fields"])
    key = _key(caller, request.get("request_id"))
    previous = store.get(key)
    if previous:
        if previous["encoded"] != encoded:
            raise Conflict("request id already used")
        return _receipt(previous)
    job = {
        "request": deepcopy(request),
        "encoded": encoded,
        "state": "received",
        "allowed_fields": list(caller["allowed_fields"]),
    }
    store[key] = job
    return _receipt(job)


def get_capture(store, caller, request_id):
    _client(store, caller)
    job = store.get(_key(caller, request_id))
    if not job:
        raise NotFound(request_id)
    return _receipt(job)


def process_capture(store, caller, request_id, *, resolver=None):
    client = _client(store, caller)
    key = _key(caller, request_id)
    job = store.get(key)
    if not job:
        raise NotFound(request_id)
    if job["state"] in ("saved", "needs_review", "failed"):
        return _receipt(job)
    if not set(job["allowed_fields"]).issubset(caller["allowed_fields"]):
        job["state"] = "needs_review"
        store[key] = job
        return _receipt(job)
    if job["state"] in ("processing", "uncertain") and job.get("phase") != "resolving":
        job["state"] = "uncertain"
        plan = job.get("resolution")
        if plan:
            try:
                row = life_hub.read_row(plan["table"], plan["identity"], list(plan["values"]))
                if row and row.get("deleted_at"):
                    job["state"] = "needs_review"
                elif row and all(
                    same_value(row.get(key), value) for key, value in plan["values"].items()
                ):
                    job["state"] = "saved"
                    job["item"] = {"kind": plan["kind"], "id": plan["identity"]}
            except Exception:
                pass
        store[key] = job
        return _receipt(job)
    job["state"] = "processing"
    job["phase"] = "resolving"
    store[key] = job
    if resolver is None:
        from core.media_resolution import resolve_capture

        resolver = resolve_capture

    def checkpoint(plan):
        job["phase"] = "writing"
        job["resolution"] = deepcopy(plan)
        store[key] = job

    try:
        kind, result = resolver(
            deepcopy(job["request"]),
            list(client["categories"]),
            list(job["allowed_fields"]),
            checkpoint=checkpoint,
        )
        if result.state == "saved" and result.identity and result.revision:
            job["state"], job["item"] = "saved", {"kind": kind, "id": result.identity}
        else:
            job["state"] = "uncertain" if result.state == "uncertain" else "needs_review"
    except Exception:
        job["state"] = "uncertain"
    store[key] = job
    return _receipt(job)


def validate_envelope(payload):
    if not isinstance(payload, dict) or payload.get("action") not in ("submit", "get"):
        raise InvalidRequest("invalid gateway request")
    required = {
        "action",
        "subject",
        "allowed_fields",
        "request" if payload["action"] == "submit" else "request_id",
    }
    if set(payload) != required:
        raise InvalidRequest("invalid gateway request")
    if len(json.dumps(payload).encode()) > 65536:
        raise InvalidRequest("capture too large")


def gateway_caller(store, authorization, payload):
    client = capture_clients.authenticate(store, authorization)
    caller = {
        **client,
        "subject": payload.get("subject"),
        "allowed_fields": payload.get("allowed_fields"),
    }
    _client(store, caller)
    return caller
