"""store.VolumeStore: the durable key -> JSON mapping on a Modal Volume."""

import contextlib
from types import SimpleNamespace

import pytest
from modal.volume import FileEntryType

from store import VolumeStore


class FakeVolume:
    def __init__(self):
        self.files = {}

    def read_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        yield self.files[path]

    @contextlib.contextmanager
    def batch_upload(self, force=False):
        assert force, "overwrites must be explicit"
        yield SimpleNamespace(put_file=lambda f, path: self.files.__setitem__(path, f.read()))

    def remove_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def listdir(self, path, recursive=False):
        return [SimpleNamespace(path=p, type=FileEntryType.FILE) for p in self.files]


def test_round_trips_values_as_json_files_grouped_by_key_kind():
    vol = FakeVolume()
    store = VolumeStore(vol)
    store["client:abc"] = {"label": "phone"}
    store["workspace:default"] = {"overlay": {}}
    assert vol.files.keys() == {"client/abc.json", "workspace/default.json"}
    assert store["client:abc"] == {"label": "phone"}
    assert store.get("client:missing") is None
    assert sorted(store.keys()) == ["client:abc", "workspace:default"]


def test_pop_and_delete_remove_the_file():
    store = VolumeStore(FakeVolume())
    store["token:h"] = "abc"
    assert store.pop("token:h") == "abc"
    with pytest.raises(KeyError):
        store.pop("token:h")
