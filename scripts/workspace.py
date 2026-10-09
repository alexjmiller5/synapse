"""Manage Synapse workspaces: one user's config overlay, Notion property ids and
credentials, kept in the deployed app's state (operator tool).

    uv run scripts/workspace.py list
    uv run scripts/workspace.py show <id>
    uv run scripts/workspace.py pull <id> <dir>       # writes overlay.yaml + property_ids.yaml
    uv run scripts/workspace.py push <id> <dir>       # validated against the template; secrets kept
    uv run scripts/workspace.py set-secrets <id>      # KEY=VALUE lines on stdin

An overlay only lists what differs from src/core/template/databases.yaml: the
workspace's Notion ids (`db_ids`, per-category `db_id`), its allowlists and
instruction wording, `tasks.place_tags`. Secrets are the workspace's own Notion
connection and soma hub: NOTION_INTEGRATION_TOKEN, SOMA_HUB_URL,
SOMA_HUB_TOKEN - pipe them in (`op inject`, `op read`), never as arguments.
A new person = push an overlay + set-secrets, then
`just clients issue "<device>" <id>` for each device.
"""

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from core import workspace  # noqa: E402
from store import open_state  # noqa: E402


def _read(folder: Path, name: str):
    path = folder / name
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else None


def main(argv: list[str]) -> int:
    store = open_state()
    match [a for a in argv if a]:
        case ["list"]:
            for wid in workspace.ids(store):
                print(wid)
        case ["show", wid]:
            print(json.dumps(workspace.summary(store, wid), indent=2, sort_keys=True))
        case ["pull", wid, folder]:
            record = store[f"workspace:{wid}"]
            out = Path(folder)
            out.mkdir(parents=True, exist_ok=True)
            (out / "overlay.yaml").write_text(
                yaml.safe_dump(record["overlay"], sort_keys=False, allow_unicode=True)
            )
            (out / "property_ids.yaml").write_text(
                yaml.safe_dump(record["property_ids"], allow_unicode=True)
            )
            print(f"wrote {out}/overlay.yaml and property_ids.yaml (credentials are not pulled)")
        case ["push", wid, folder]:
            src = Path(folder)
            overlay = _read(src, "overlay.yaml")
            if overlay is None:
                print(f"{src}/overlay.yaml not found", file=sys.stderr)
                return 1
            workspace.save(
                store, wid, overlay=overlay, property_ids=_read(src, "property_ids.yaml")
            )
            print(f"pushed workspace {wid}")
        case ["set-secrets", wid]:
            secrets = {}
            for line in sys.stdin.read().splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip():
                    secrets[key.strip().lower()] = value.strip()
            workspace.save(store, wid, secrets=secrets)
            print(f"set {sorted(secrets)} on workspace {wid}")
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
