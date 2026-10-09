"""The active workspace's config, as live views.

PROMPTS (prompts.yaml merged with the workspace overlay) and CATEGORIES (its
`categories` section) are views of `workspace.current()`: every read goes to
whichever workspace is active (see core/workspace.py), so one process can
serve many workspaces.
"""

from collections.abc import MutableMapping

from core import workspace


class _View(MutableMapping):
    def __init__(self, data):
        self._data = data

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
        return f"<config view of workspace {workspace.current().id!r}>"


PROMPTS = _View(lambda: workspace.current().config)
CATEGORIES = _View(lambda: workspace.current().config.setdefault("categories", {}))
