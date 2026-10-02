"""The active workspace's config, under the names the pipeline has always used.

DATABASES, PROMPTS and PROPERTY_IDS are live views of `workspace.current()`:
every read goes to whichever workspace is active (see core/workspace.py), so
`from core.config import DATABASES` keeps working while one process serves
many workspaces.
"""

from collections.abc import MutableMapping

from core import workspace


class _View(MutableMapping):
    def __init__(self, attr: str):
        self._attr = attr

    def _data(self) -> dict:
        return getattr(workspace.current(), self._attr)

    def __getitem__(self, key):
        return self._data()[key]

    def __setitem__(self, key, value):
        self._data()[key] = value

    def __delitem__(self, key):
        del self._data()[key]

    def __iter__(self):
        return iter(self._data())

    def __len__(self):
        return len(self._data())

    def __repr__(self):
        return f"<{self._attr} of workspace {workspace.current().id!r}>"


DATABASES = _View("databases")
PROMPTS = _View("prompts")
# {category: {prop_name: stable_prop_id}} — generated per workspace by
# scripts/fetch_property_ids.py. Missing entries fall back to the name (prop_id).
PROPERTY_IDS = _View("property_ids")
