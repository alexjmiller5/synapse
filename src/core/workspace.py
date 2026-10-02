"""Workspaces: whose Notion, life-data hub and taxonomy a capture is filed into.

The repo ships only the product: `template/databases.yaml` (categories,
properties, extraction instructions, generic allowlists) and
`template/prompts.yaml`. Everything that belongs to one user - their Notion
ids, their allowlists and wording, their place tags, their Notion and
life-data credentials, their property-id map - is a workspace, stored by the
running service (a modal.Dict in production) and edited with
`scripts/workspace.py`, never committed.

A workspace's config is the template deep-merged with its overlay (dicts
merge, lists and scalars replace). Code reads the active workspace through
`current()` - or through core.config's DATABASES / PROMPTS / PROPERTY_IDS,
which are live views of it - so one process can serve many workspaces:
`with use(load(store, id)):` around each capture.

Without an active workspace (tests, local scripts) `current()` is the local
one: the overlay and property ids from $SYNAPSE_WORKSPACE_DIR, if set, and
credentials from the matching env vars.
"""

import contextlib
import copy
import os
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

TEMPLATE_DIR = Path(__file__).parent / "template"
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
DEFAULT_ID = "default"
# A workspace's credentials: its own Notion connection and life-data hub.
# The app's own provider keys (Gemini, TMDB, Spotify, YouTube) stay in env.
SECRET_NAMES = ("notion_integration_token", "life_hub_url", "life_hub_token")


class InvalidOverlay(ValueError):
    pass


class UnknownWorkspace(KeyError):
    pass


@dataclass
class Workspace:
    id: str
    databases: dict
    prompts: dict
    property_ids: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict)


@lru_cache
def template() -> dict:
    return yaml.safe_load((TEMPLATE_DIR / "databases.yaml").read_text()) or {}


@lru_cache
def _prompts() -> dict:
    return yaml.safe_load((TEMPLATE_DIR / "prompts.yaml").read_text()) or {}


def merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _check(overlay: dict, template: dict) -> None:
    categories = template.get("databases", {})
    for name, stanza in (overlay.get("databases") or {}).items():
        if name not in categories:
            raise InvalidOverlay(f"category {name!r} is not in the template")
        props = categories[name].get("properties", {})
        for prop in (stanza or {}).get("properties") or {}:
            if prop not in props:
                raise InvalidOverlay(f"{name}.{prop!r} is not a template property")


def build(
    id: str, overlay: dict | None = None, property_ids=None, secrets=None, template=None
) -> Workspace:
    overlay = overlay or {}
    base = globals()["template"]() if template is None else template
    _check(overlay, base)
    databases = merge(base, overlay)
    tasks = databases.get("databases", {}).get("tasks") or {}
    # Place tags ("do when next at X") are Tags options too.
    allow = tasks.get("properties", {}).get("Tags", {}).get("allowlist")
    if tasks.get("place_tags") and allow is not None:
        allow.extend(t for t in tasks["place_tags"] if t not in allow)
    return Workspace(
        id, databases, copy.deepcopy(_prompts()), dict(property_ids or {}), dict(secrets or {})
    )


# --- the service's store: {"workspace:<id>": {overlay, property_ids, secrets, updated_at}} ---


def save(store, id: str, overlay=None, property_ids=None, secrets=None) -> None:
    """Replace whichever parts are given; the others are kept."""
    if not ID.fullmatch(id or ""):
        raise InvalidOverlay(f"workspace id {id!r}: lowercase letters, digits, dashes")
    if secrets is not None and (bad := sorted(set(secrets) - set(SECRET_NAMES))):
        raise InvalidOverlay(f"unknown secret names {bad}; allowed {list(SECRET_NAMES)}")
    record = dict(
        store.get(f"workspace:{id}") or {"overlay": {}, "property_ids": {}, "secrets": {}}
    )
    if overlay is not None:
        build(id, overlay)  # refuse an overlay that does not fit the template
        record["overlay"] = overlay
    if property_ids is not None:
        record["property_ids"] = property_ids
    if secrets is not None:
        record["secrets"] = {**record["secrets"], **secrets}
    record["updated_at"] = int(time.time())
    store[f"workspace:{id}"] = record


def load(store, id: str) -> Workspace:
    record = store.get(f"workspace:{id}")
    if not record:
        raise UnknownWorkspace(id)
    return build(id, record["overlay"], record["property_ids"], record["secrets"])


def summary(store, id: str) -> dict:
    record = store.get(f"workspace:{id}")
    if not record:
        raise UnknownWorkspace(id)
    return {
        "id": id,
        "overlay": record["overlay"],
        "property_ids": sorted(record["property_ids"]),
        "secrets_set": sorted(k for k, v in record["secrets"].items() if v),
        "updated_at": record.get("updated_at"),
    }


def ids(store) -> list[str]:
    return sorted(k.split(":", 1)[1] for k in store.keys() if k.startswith("workspace:"))


# --- the active workspace ---


@lru_cache
def local() -> Workspace:
    folder = os.environ.get("SYNAPSE_WORKSPACE_DIR")

    def read(name):
        path = Path(folder or "") / name
        return (yaml.safe_load(path.read_text()) or {}) if folder and path.exists() else {}

    secrets = {name: os.environ.get(name.upper()) for name in SECRET_NAMES}
    return build(DEFAULT_ID, read("overlay.yaml"), read("property_ids.yaml"), secrets)


_active: ContextVar[Workspace | None] = ContextVar("synapse_workspace", default=None)


def current() -> Workspace:
    return _active.get() or local()


@contextlib.contextmanager
def use(workspace: Workspace):
    token = _active.set(workspace)
    try:
        yield workspace
    finally:
        _active.reset(token)
