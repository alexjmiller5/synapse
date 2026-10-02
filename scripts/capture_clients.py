"""Issue, list and revoke Synapse's per-device capture tokens (operator tool).

    uv run scripts/capture_clients.py issue "<device label>" [workspace]   # prints the enrollment link
    uv run scripts/capture_clients.py list
    uv run scripts/capture_clients.py revoke <client_id>

Needs Modal auth for the Modal workspace that runs Synapse (MODAL_TOKEN_ID /
MODAL_TOKEN_SECRET or a modal profile). A token files captures into one
Synapse workspace (default: "default"). The link is the only copy of the
token: send it to the device owner, who opens it on that device.
"""

import sys

import modal

sys.path.insert(0, "src")
sys.path.insert(0, ".")
from core import capture_clients, workspace  # noqa: E402
from store import open_state  # noqa: E402

APP_NAME = "synapse"


def main(argv: list[str]) -> int:
    store = open_state()
    match [a for a in argv if a]:
        case ["issue", label, *rest] if len(rest) <= 1:
            wid = rest[0] if rest else workspace.DEFAULT_ID
            workspace.summary(store, wid)  # refuse a token for a workspace that does not exist
            issued = capture_clients.issue(store, label, workspace=wid)
            enroll = modal.Function.from_name(APP_NAME, "enroll").get_web_url()
            capture = modal.Function.from_name(APP_NAME, "capture").get_web_url()
            print(f"client_id: {issued['client_id']}  label: {issued['label']}  workspace: {wid}")
            print(capture_clients.enrollment_link(enroll, capture, issued["token"]))
        case ["list"]:
            for c in capture_clients.list_clients(store):
                state = "revoked" if c["revoked"] else "active"
                print(
                    f"{c['client_id']}  {state:7}  {c.get('workspace', 'default'):12}  {c['label']}"
                )
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
