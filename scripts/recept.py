#!/usr/bin/env python3
# /// script
# dependencies = ["requests"]
# ///
"""Send one thought to the deployed Synapse webhook (Modal).

Env vars: MODAL_WEBHOOK_URL, MODAL_PROXY_TOKEN_ID, MODAL_PROXY_TOKEN_SECRET.
Run via `just recept "your text"` (fills them from 1Password).
"""

import os
import sys

import requests


def send_event(input_text):
    url = os.environ.get("MODAL_WEBHOOK_URL")
    if not url:
        print("❌ MODAL_WEBHOOK_URL is not set.")
        sys.exit(1)

    headers = {
        "Modal-Key": os.environ.get("MODAL_PROXY_TOKEN_ID", ""),
        "Modal-Secret": os.environ.get("MODAL_PROXY_TOKEN_SECRET", ""),
    }

    print(f"🚀 Sending: '{input_text}'...")
    try:
        response = requests.post(url, json={"raw_text": input_text}, headers=headers)
        response.raise_for_status()
        print(f"✅ Success: {response.status_code}")
        print(response.text)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: recept.py <text>")
        sys.exit(1)

    send_event(" ".join(sys.argv[1:]))
