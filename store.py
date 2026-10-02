"""Durable key -> JSON store on a Modal Volume: the Modal half of the `store`
mappings core/capture_clients.py and core/workspace.py take.

Each key `kind:name` is the file `kind/name.json`. Reads go through the Volume
API, so they always see the latest committed write from any container or the
operator's machine. (A modal.Dict would be simpler, but its entries expire
after 7 days without reads or writes - an idle device's token or an idle
workspace would silently vanish.)
"""

import io
import json
from collections.abc import MutableMapping

from modal.volume import FileEntryType


class VolumeStore(MutableMapping):
    def __init__(self, volume):
        self.volume = volume

    @staticmethod
    def _path(key: str) -> str:
        kind, _, name = key.partition(":")
        return f"{kind}/{name}.json"

    def __getitem__(self, key):
        try:
            return json.loads(b"".join(self.volume.read_file(self._path(key))))
        except FileNotFoundError:
            raise KeyError(key) from None

    def __setitem__(self, key, value):
        with self.volume.batch_upload(force=True) as upload:
            upload.put_file(io.BytesIO(json.dumps(value).encode()), self._path(key))

    def __delitem__(self, key):
        try:
            self.volume.remove_file(self._path(key))
        except FileNotFoundError:
            raise KeyError(key) from None

    def __iter__(self):
        for entry in self.volume.listdir("/", recursive=True):
            if entry.type == FileEntryType.FILE and entry.path.endswith(".json"):
                kind, _, name = entry.path.removesuffix(".json").partition("/")
                yield f"{kind}:{name}"

    def __len__(self):
        return sum(1 for _ in self)


STATE_VOLUME = "synapse-state"


def open_state() -> VolumeStore:
    """The deployed app's state, from the operator's machine (Modal auth via
    MODAL_TOKEN_ID / MODAL_TOKEN_SECRET or a modal profile)."""
    import modal

    return VolumeStore(modal.Volume.from_name(STATE_VOLUME, create_if_missing=True))


def activate(workspace_id: str | None):
    """Run local tools inside a stored workspace ($SYNAPSE_WORKSPACE), or the
    local one (template + $SYNAPSE_WORKSPACE_DIR + env) when none is named."""
    import contextlib

    from core import workspace

    if not workspace_id:
        return contextlib.nullcontext(workspace.current())
    return workspace.use(workspace.load(open_state(), workspace_id))
