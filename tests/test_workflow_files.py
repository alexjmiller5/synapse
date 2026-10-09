import copy
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from core.soma_hub import retain_text
from core.workflow import WorkflowWriter

SETTINGS = SimpleNamespace(soma_hub_url="https://hub.example", soma_hub_token="synthetic")


@pytest.mark.parametrize("status", [201, 412])
def test_retained_file_is_conditional_and_byte_verified(status):
    raw = "Original \u03b1\n" * 100
    digest = hashlib.sha256(raw.encode()).hexdigest()
    client = Mock()
    put = Mock(status_code=status)
    put.json.return_value = {
        "key": "raw/app/value.txt",
        "bytes": len(raw.encode()),
        "sha256": digest,
    }
    client.put.return_value = put
    client.get.return_value = Mock(content=raw.encode())
    assert (
        retain_text("raw/app/value.txt", raw, settings=SETTINGS, client=client)
        == "/v1/files/raw/app/value.txt"
    )
    call = client.put.call_args
    assert call.kwargs["data"] == raw.encode()
    assert call.kwargs["headers"]["If-None-Match"] == "*"
    assert call.kwargs["headers"]["X-Content-SHA256"] == digest
    assert client.get.call_args.args[0] == "https://hub.example/v1/files/raw/app/value.txt"


def test_wrong_existing_file_content_prevents_reference_delivery():
    client = Mock()
    client.put.return_value = Mock(status_code=412)
    client.get.return_value = Mock(content=b"other")
    with pytest.raises(RuntimeError, match="content"):
        retain_text("raw/app/value.txt", "original", settings=SETTINGS, client=client)


@pytest.mark.parametrize(
    "key", ["../escape", "raw//bad", "/absolute", "raw/../bad", "raw/value?query"]
)
def test_retention_refuses_noncanonical_keys_before_network(key):
    client = Mock()
    with pytest.raises(ValueError):
        retain_text(key, "text", settings=SETTINGS, client=client)
    client.put.assert_not_called()


def test_oversized_text_is_frozen_before_upload_and_retry_preserves_original():
    binding = {
        "table": "logs",
        "columns": {"Raw": "raw", "Result": "result"},
        "retained_fields": ["Raw"],
        "max_inline_bytes": 1024,
        "files_prefix": "raw/app",
    }
    store, files, rows = {}, {}, []
    original = "Long \u03b1 input\n" * 1000

    def retain(key, value):
        assert store
        files[key] = value
        return "/v1/files/" + key

    def send(table, batch):
        rows.append(copy.deepcopy(batch[0]))
        if len(rows) == 1:
            raise requests.Timeout("committed response lost")

    first = WorkflowWriter(store, "sample", "capture", send=send, retain=retain)
    with pytest.raises(requests.Timeout):
        first.create(binding, "execution", {"Raw": original, "Result": "Success"})
    WorkflowWriter(store, "sample", "capture", send=send, retain=retain).create(
        binding, "execution", {"Raw": "changed", "Result": "Changed"}
    )
    assert len(files) == 1 and next(iter(files.values())) == original
    assert rows[0] == rows[1]
    assert rows[0]["result"] == "Success"
    assert "/v1/files/raw/app/" in rows[0]["raw"]
    assert hashlib.sha256(original.encode()).hexdigest() in rows[0]["raw"]


def test_failed_retention_never_creates_dangling_row_reference():
    store, send = {}, Mock()
    retain = Mock(side_effect=requests.Timeout("upload unavailable"))
    binding = {
        "table": "logs",
        "columns": {"Raw": "raw"},
        "retained_fields": ["Raw"],
        "max_inline_bytes": 1024,
        "files_prefix": "raw/app",
    }
    with pytest.raises(requests.Timeout):
        WorkflowWriter(store, "sample", "capture", send=send, retain=retain).create(
            binding, "execution", {"Raw": "x" * 2000}
        )
    assert store
    send.assert_not_called()
