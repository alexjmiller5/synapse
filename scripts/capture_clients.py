"""Issue, list and revoke Synapse's per-device capture tokens (operator tool).

    uv run scripts/capture_clients.py issue "<device label>"   # prints the enrollment link
    uv run scripts/capture_clients.py list
    uv run scripts/capture_clients.py revoke <client_id>

Needs Modal auth for the workspace that runs Synapse (MODAL_TOKEN_ID /
MODAL_TOKEN_SECRET or a modal profile). The link is the only copy of the
token: send it to the device owner, who opens it on that device.
"""

import sys

import modal

sys.path.insert(0, "src")
from core import capture_clients  # noqa: E402

APP_NAME = "synapse"


def main(argv: list[str]) -> int:
    store = modal.Dict.from_name(f"{APP_NAME}-capture-clients")
    match [a for a in argv if a]:
        case ["issue", label]:
            issued = capture_clients.issue(store, label)
            enroll = modal.Function.from_name(APP_NAME, "enroll").get_web_url()
            capture = modal.Function.from_name(APP_NAME, "capture").get_web_url()
            print(f"client_id: {issued['client_id']}  label: {issued['label']}")
            print(capture_clients.enrollment_link(enroll, capture, issued["token"]))
        case ["list"]:
            for c in capture_clients.list_clients(store):
                state = "revoked" if c["revoked"] else "active"
                print(f"{c['client_id']}  {state:7}  {c['label']}")
        case ["revoke", client_id]:
            if not capture_clients.revoke(store, client_id):
                print(f"no client {client_id}", file=sys.stderr)
                return 1
            print(f"revoked {client_id}")
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
