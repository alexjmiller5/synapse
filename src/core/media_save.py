"""Lossless media capture over the supported hub row API."""

import json
from dataclasses import dataclass
from typing import Literal

import requests

from core.timeutils import now_utc_iso_ms
from core.life_hub import InsertRejected


@dataclass(frozen=True)
class SaveReceipt:
    state: Literal["saved", "needs_review", "conflict", "uncertain"]
    identity: str
    revision: dict | None = None
    reason: str | None = None


def same_value(actual, expected):
    if isinstance(expected, (list, dict)) and isinstance(actual, str):
        try:
            actual = json.loads(actual)
        except ValueError:
            return False
    return actual == expected


def save_media(
    hub,
    binding,
    identity,
    initializer,
    requested_values,
    expected_revision=None,
    *,
    checkpoint=None,
):
    """Insert once or conditionally patch only explicitly requested user fields.

    A timeout is uncertain even if a later read may reconcile it. This function
    never retries a mutation or substitutes an unconditional push.
    """
    reserved = {"id", "created_at", "updated_at", "hub_at", "deleted_at"}
    if not isinstance(identity, str) or not identity.strip():
        return SaveReceipt("needs_review", identity, reason="identity_required")
    if reserved.intersection(requested_values) or not set(requested_values).issubset(
        binding["editable_columns"]
    ):
        return SaveReceipt("needs_review", identity, reason="field_not_editable")
    table = binding["table"]
    if checkpoint:
        checkpoint({"table": table, "identity": identity, "values": requested_values})
    columns = sorted(set(initializer) | set(requested_values) | reserved)
    try:
        row = hub.read_row(table, identity, columns)
        if row is None:
            if expected_revision is not None:
                return SaveReceipt("conflict", identity, reason="record_missing")
            new = {
                **initializer,
                **requested_values,
                "id": identity,
                "updated_at": now_utc_iso_ms(),
            }
            new.pop("hub_at", None)
            new.pop("deleted_at", None)
            result = hub.insert_rows(table, [new])
            if result.get("rejected"):
                return SaveReceipt("needs_review", identity, reason="insert_rejected")
            row = hub.read_row(table, identity, columns)
            if row is None:
                return SaveReceipt("uncertain", identity, reason="insert_not_observed")
        if row.get("deleted_at"):
            return SaveReceipt("needs_review", identity, reason="record_deleted")
        revision = {column: row.get(column) for column in ("updated_at", "hub_at")}
        if expected_revision is not None and revision != expected_revision:
            return SaveReceipt("conflict", identity, reason="revision_conflict")
        values = {
            key: value
            for key, value in requested_values.items()
            if not same_value(row.get(key), value)
        }
        if not values:
            return SaveReceipt("saved", identity, revision)
        result = hub.patch_row(table, identity, values, revision)
        return SaveReceipt("saved", identity, result["revision"])
    except InsertRejected:
        return SaveReceipt("needs_review", identity, reason="insert_rejected")
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        if status == 409:
            return SaveReceipt("conflict", identity, reason="revision_conflict")
        if status in (400, 401, 403, 404, 422):
            return SaveReceipt("needs_review", identity, reason="request_rejected")
        return SaveReceipt("uncertain", identity, reason="service_unavailable")
    except (requests.RequestException, ValueError, KeyError, RuntimeError):
        return SaveReceipt("uncertain", identity, reason="reply_unavailable")
