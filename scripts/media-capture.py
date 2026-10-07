"""Mint a dedicated media gateway credential in the service's durable store.

Run from the project root. The JSON output contains the token exactly once;
pipe it directly into the caller service's supported secret storage.
"""

import argparse
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, ".")
from core import capture_clients, workspace  # noqa: E402
from store import open_state  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--categories", nargs="+", required=True)
    parser.add_argument("--fields", nargs="+", required=True)
    args = parser.parse_args()
    store = open_state()
    workspace.summary(store, args.workspace)
    print(
        json.dumps(
            capture_clients.issue_gateway(
                store, args.label, args.workspace, args.categories, args.fields
            )
        )
    )


if __name__ == "__main__":
    main()
