#!/usr/bin/env python3
"""
Reprocess unremedied Synapse Executions.

Fetches execution log entries with Outcome = "Failed Extraction", "Bug", or
"Error(s)" that haven't been remedied, and sends their Raw Input through the
local processor for reprocessing.

Usage:
    uv run scripts/reprocess_unremedied.py --dry-run
    uv run scripts/reprocess_unremedied.py
    uv run scripts/reprocess_unremedied.py --ids "id1,id2,id3"
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time

import requests

NOTION_VERSION = "2022-06-28"
LOGS_DB_ID = "2b103953a8af803280cec633c91c46c3"


def get_notion_key():
    key = os.environ.get("NOTION_API_KEY")
    if key:
        return key
    try:
        result = subprocess.run(
            ["op", "read", "op://OpenClaw/OpenClaw Notion Internal Integration Secret/credential"],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"❌ Cannot get Notion API key: {e}")
        sys.exit(1)


def query_unremedied(notion_key, outcome_filter):
    headers = {
        "Authorization": f"Bearer {notion_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    all_results, has_more, start_cursor = [], True, None
    while has_more:
        body = {
            "filter": {
                "and": [
                    {"property": "Code Execution", "status": {"equals": outcome_filter}},
                    {"property": "Reported", "checkbox": {"equals": False}},
                ]
            },
            "page_size": 100,
        }
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{LOGS_DB_ID}/query",
            headers=headers, json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        all_results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
    return all_results


def extract_raw_input(page):
    title_prop = page.get("properties", {}).get("Raw Input", {}).get("title", [])
    return title_prop[0].get("plain_text", "") if title_prop else ""


def send_to_processor(raw_input, processor_url="http://localhost:8080"):
    encoded = base64.b64encode(raw_input.encode("utf-8")).decode("utf-8")
    payload = {"message": {"data": encoded, "attributes": {}}}
    headers = {
        "Content-Type": "application/json",
        "Ce-Id": "reprocess",
        "Ce-Specversion": "1.0",
        "Ce-Type": "google.cloud.pubsub.topic.v1.messagePublished",
        "Ce-Source": "//pubsub.googleapis.com/projects/synapse/topics/reprocess",
    }
    return requests.post(processor_url, headers=headers, json=payload).status_code


def mark_reported(notion_key, page_id):
    headers = {
        "Authorization": f"Bearer {notion_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=headers, json={"properties": {"Reported": {"checkbox": True}}},
    )
    return resp.status_code == 200


def main():
    parser = argparse.ArgumentParser(description="Reprocess unremedied Synapse items")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ids", type=str, help="Comma-separated execution IDs")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--mark-reported", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    notion_key = get_notion_key()

    print("📋 Fetching unremedied entries...")
    failed = query_unremedied(notion_key, "Failed Extraction")
    bugs = query_unremedied(notion_key, "Bug")
    errors = query_unremedied(notion_key, "Error(s)")
    all_entries = failed + bugs + errors
    print(f"   Found {len(failed)} Failed Extractions, {len(bugs)} Bugs, {len(errors)} Errors")
    print(f"   Total: {len(all_entries)} entries")

    if args.ids:
        target_ids = set(args.ids.split(","))
        all_entries = [e for e in all_entries if e["id"] in target_ids]
        print(f"   Filtered to {len(all_entries)} entries by ID")

    if not all_entries:
        print("✅ No unremedied entries found.")
        return

    success, failed_count = 0, 0
    for i, entry in enumerate(all_entries, 1):
        page_id = entry["id"]
        raw_input = extract_raw_input(entry)
        status_name = entry.get("properties", {}).get("Code Execution", {}).get("status", {}).get("name", "Unknown")
        cat = entry.get("properties", {}).get("Category", {}).get("select", {})
        cat_name = cat.get("name", "Unknown") if cat else "Unknown"

        print(f"\n[{i}/{len(all_entries)}] {status_name} | {cat_name}")
        print(f"   Input: {raw_input[:100]}{'...' if len(raw_input) > 100 else ''}")

        if args.dry_run:
            print("   ⏭️  (dry run)")
            continue

        try:
            code = send_to_processor(raw_input, args.url)
            if code == 200:
                print(f"   ✅ Reprocessed")
                success += 1
                if args.mark_reported:
                    mark_reported(notion_key, page_id)
            else:
                print(f"   ❌ Processor returned {code}")
                failed_count += 1
        except requests.ConnectionError:
            print(f"   ❌ Cannot connect to {args.url}. Run: just run-processor")
            failed_count += 1
        except Exception as e:
            print(f"   ❌ Error: {e}")
            failed_count += 1

        if i < len(all_entries):
            time.sleep(args.delay)

    if not args.dry_run:
        print(f"\n📊 Results: {success} succeeded, {failed_count} failed")


if __name__ == "__main__":
    main()
