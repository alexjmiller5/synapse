"""App-issued capture tokens for Synapse's clients (Receptor and friends).

Each device gets its own revocable bearer token, minted here and handed over
through an enrollment link - never the operator's Modal proxy credentials -
and bound to the workspace its captures are filed into (core/workspace.py).
Only a SHA-256 of each token is stored. `store` is any mutable mapping: a
modal.Dict in production, a plain dict in tests.
"""

import hmac
import secrets
import time
from hashlib import sha256
from urllib.parse import urlencode

MAX_LABEL_LEN = 80


class Unauthorized(Exception):
    pass


class InvalidRequest(ValueError):
    pass


def _hash(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def issue(store, label, workspace: str = "default") -> dict:
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > MAX_LABEL_LEN:
        raise InvalidRequest(f"label must be 1-{MAX_LABEL_LEN} characters")
    token = secrets.token_urlsafe(32)
    client_id = secrets.token_hex(8)
    store[f"client:{client_id}"] = {
        "client_id": client_id,
        "label": label.strip(),
        "workspace": workspace,
        "token_hash": _hash(token),
        "created_at": int(time.time()),
        "revoked": False,
    }
    store[f"token:{_hash(token)}"] = client_id
    return {"client_id": client_id, "label": label.strip(), "workspace": workspace, "token": token}


def authenticate(store, authorization: str | None) -> dict:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme != "Bearer" or not token:
        raise Unauthorized
    client_id = store.get(f"token:{_hash(token)}")
    client = store.get(f"client:{client_id}") if client_id else None
    if (
        not client
        or client["revoked"]
        or not hmac.compare_digest(client["token_hash"], _hash(token))
    ):
        raise Unauthorized
    return {
        "client_id": client["client_id"],
        "label": client["label"],
        "workspace": client.get("workspace", "default"),
    }


def revoke(store, client_id: str) -> bool:
    client = store.get(f"client:{client_id}")
    if not client:
        return False
    store[f"client:{client_id}"] = {**client, "revoked": True}
    try:
        store.pop(f"token:{client['token_hash']}")
    except KeyError:
        pass
    return True


def list_clients(store) -> list[dict]:
    return sorted(
        (
            {k: v for k, v in value.items() if k != "token_hash"}
            for key, value in store.items()
            if key.startswith("client:")
        ),
        key=lambda c: c["created_at"],
    )


def enrollment_link(enroll_page_url: str, capture_url: str, token: str) -> str:
    """The token rides in the URL fragment, which browsers never send to the
    page's server, so it stays out of request logs."""
    return f"{enroll_page_url}#{urlencode({'url': capture_url, 'token': token})}"
