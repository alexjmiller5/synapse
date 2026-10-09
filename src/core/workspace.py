"""Workspaces: whose Soma hub and phrasing a capture is filed with.

The repo ships only the product: `template/prompts.yaml` (the prompt templates
and each category's table wiring and phrasing instructions). Column contracts
(types, required, options and their meanings, defaults) come from the Soma
catalog at run time (core/catalog.py), never from a copy here. Everything that
belongs to one user - their workflow table bindings, wording overrides, place
tags and hub credentials - is a workspace, stored by the running service (a
store.VolumeStore in production) and edited with `scripts/workspace.py`,
never committed.

A workspace's config is the template deep-merged with its overlay (dicts
merge, lists and scalars replace). Code reads the active workspace through
`current()` - or through core.config's PROMPTS / CATEGORIES, which are live
views of it - so one process can serve many workspaces:
`with use(load(store, id)):` around each capture.

Without an active workspace (tests, local scripts) `current()` is the local
one: the overlay from $SYNAPSE_WORKSPACE_DIR, if set, and credentials from
the matching env vars.
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
# A workspace's credentials: its own Soma hub.
# The app's own provider keys (Gemini, TMDB, Spotify, YouTube) stay in env.
SECRET_NAMES = ("soma_hub_url", "soma_hub_token")
# Workspaces saved before the hub was named Soma hold its credentials under
# these keys; they are read as the current names and rewritten on the next save.
LEGACY_SECRET_NAMES = {"life_hub_url": "soma_hub_url", "life_hub_token": "soma_hub_token"}


def _adopt(secrets: dict) -> dict:
    out = dict(secrets)
    for old, new in LEGACY_SECRET_NAMES.items():
        if old in out:
            out.setdefault(new, out.pop(old))
    return {k: v for k, v in out.items() if k in SECRET_NAMES}


class InvalidOverlay(ValueError):
    pass


class UnknownWorkspace(KeyError):
    pass


@dataclass
class Workspace:
    id: str
    config: dict
    secrets: dict = field(default_factory=dict)
    # The operational store the catalog cache lives in (None: no cache).
    store: object = None
    catalog_memo: dict | None = None


@lru_cache
def template() -> dict:
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
    categories = template.get("categories", {})
    for name, stanza in (overlay.get("categories") or {}).items():
        if name not in categories:
            raise InvalidOverlay(f"category {name!r} is not in the template")
        props = categories[name].get("properties", {})
        for prop in (stanza or {}).get("properties") or {}:
            if prop not in props:
                raise InvalidOverlay(f"{name}.{prop!r} is not a template property")


def build(
    id: str, overlay: dict | None = None, secrets=None, template=None, store=None
) -> Workspace:
    overlay = overlay or {}
    base = globals()["template"]() if template is None else template
    _check(overlay, base)
    config = merge(base, overlay)
    tasks = config.get("categories", {}).get("tasks") or {}
    # Place tags ("do when next at X") are Tags options too.
    allow = tasks.get("properties", {}).get("Tags", {}).get("allowlist")
    if tasks.get("place_tags") and allow is not None:
        allow.extend(t for t in tasks["place_tags"] if t not in allow)
    return Workspace(id, config, dict(secrets or {}), store)


# --- the service's store: {"workspace:<id>": {overlay, secrets, updated_at}} ---


def save(store, id: str, overlay=None, secrets=None) -> None:
    """Replace whichever parts are given; the others are kept."""
    if not ID.fullmatch(id or ""):
        raise InvalidOverlay(f"workspace id {id!r}: lowercase letters, digits, dashes")
    if secrets is not None and (bad := sorted(set(secrets) - set(SECRET_NAMES))):
        raise InvalidOverlay(f"unknown secret names {bad}; allowed {list(SECRET_NAMES)}")
    stored = store.get(f"workspace:{id}") or {}
    record = {
        "overlay": stored.get("overlay", {}),
        "secrets": {**_adopt(stored.get("secrets", {})), **(secrets or {})},
    }
    if overlay is not None:
        build(id, overlay)  # refuse an overlay that does not fit the template
        record["overlay"] = overlay
    record["updated_at"] = int(time.time())
    store[f"workspace:{id}"] = record


def load(store, id: str) -> Workspace:
    record = store.get(f"workspace:{id}")
    if not record:
        raise UnknownWorkspace(id)
    return build(id, record["overlay"], _adopt(record["secrets"]), store=store)


def summary(store, id: str) -> dict:
    record = store.get(f"workspace:{id}")
    if not record:
        raise UnknownWorkspace(id)
    return {
        "id": id,
        "overlay": record["overlay"],
        "secrets_set": sorted(k for k, v in _adopt(record["secrets"]).items() if v),
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
    return build(DEFAULT_ID, read("overlay.yaml"), secrets)


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
