# /// script
# dependencies = ["requests"]
# ///
"""Batch-send thoughts to the deployed Synapse webhook (Modal), one per line.

Env vars: MODAL_WEBHOOK_URL, MODAL_PROXY_TOKEN_ID, MODAL_PROXY_TOKEN_SECRET.
Run via `just recept-batch` (fills them from 1Password).
"""

import argparse
import os
import sys

import requests

# ANSI Colors for terminal output
GREEN = "\033[0;32m"
RED = "\033[0;31m"
NC = "\033[0m"


def process_request(content, url, headers):
    # Skip empty/whitespace only strings
    if not content or not content.strip():
        return

    # Show preview (truncated to 50 chars)
    preview = content.strip().replace("\n", " ")[:50]
    print(f"Processing: {preview}...", end="", flush=True)

    try:
        response = requests.post(url, json={"raw_text": content}, headers=headers)
        if response.ok:
            print(f" {GREEN}✅ Success{NC}")
        else:
            print(f" {RED}❌ Failed ({response.status_code}){NC}")
    except Exception as e:
        print(f" {RED}❌ Error: {e}{NC}")


def main():
    parser = argparse.ArgumentParser(description="Batch process requests to the Synapse webhook.")
    parser.add_argument("filename", help="Input file containing requests")
    parser.add_argument(
        "--endpoint",
        "-e",
        default=os.environ.get("MODAL_WEBHOOK_URL"),
        help="Target endpoint URL (default: $MODAL_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--separator", "-s", default="\n", help="Separator between requests (default: newline)"
    )

    args = parser.parse_args()

    if not args.endpoint:
        print(f"{RED}Error: MODAL_WEBHOOK_URL not set and no --endpoint given.{NC}")
        sys.exit(1)

    if not os.path.exists(args.filename):
        print(f"{RED}Error: File {args.filename} not found.{NC}")
        sys.exit(1)

    headers = {
        "Modal-Key": os.environ.get("MODAL_PROXY_TOKEN_ID", ""),
        "Modal-Secret": os.environ.get("MODAL_PROXY_TOKEN_SECRET", ""),
    }

    print(f"⚡️ Starting batch process from {args.filename} to {args.endpoint}...")

    try:
        with open(args.filename, "r", encoding="utf-8") as f:
            file_content = f.read()

        # Logic to split by separator
        if args.separator in ("\\n", "\n"):
            requests_list = file_content.splitlines()
        else:
            requests_list = file_content.split(args.separator)

        for content in requests_list:
            process_request(content, args.endpoint, headers)

    except KeyboardInterrupt:
        print("\n🛑 Process cancelled.")
        sys.exit(0)

    print("🎉 Batch complete.")


if __name__ == "__main__":
    main()
