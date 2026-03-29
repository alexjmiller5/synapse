#!/usr/bin/env python3
"""
One-time backfill script to set website favicons as Notion page icons
for all existing bookmarks in the Bookmarks database.

Uses Google's favicon service v2 (t2.gstatic.com/faviconV2) to resolve
favicons by domain. Falls back to a generic globe icon when no favicon exists.

Usage:
    # Set NOTION_API_KEY env var or pass via --token
    export NOTION_API_KEY=$(op read "op://OpenClaw/OpenClaw Notion Internal Integration Secret/credential")
    python scripts/backfill_bookmark_favicons.py

    # Dry run (no changes)
    python scripts/backfill_bookmark_favicons.py --dry-run

    # Limit to N bookmarks (for testing)
    python scripts/backfill_bookmark_favicons.py --limit 5
"""

import argparse
import os
import sys
import time
from urllib.parse import urlparse

import requests

BOOKMARKS_DATA_SOURCE_ID = "2a803953-a8af-80bf-a145-000b8cf4f5e0"
NOTION_VERSION = "2026-03-11"
NOTION_BASE_URL = "https://api.notion.com/v1"
FAVICON_TEMPLATE = "https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://{domain}&size=128"
GITHUB_CUSTOM_EMOJI_ID = "2d103953-a8af-8072-b828-007aa3901d27"  # "github-light" custom emoji

# Notion rate limit: 3 requests/sec average
REQUEST_DELAY = 0.35  # seconds between PATCH requests


def get_notion_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def fetch_all_bookmarks(headers: dict) -> list:
    """Fetch all bookmark pages from Notion, paginating through all results."""
    bookmarks = []
    has_more = True
    next_cursor = None

    while has_more:
        body = {"page_size": 100}
        if next_cursor:
            body["start_cursor"] = next_cursor

        resp = requests.post(
            f"{NOTION_BASE_URL}/data_sources/{BOOKMARKS_DATA_SOURCE_ID}/query",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        bookmarks.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor")

        print(f"  Fetched {len(bookmarks)} bookmarks so far...")

    return bookmarks


def extract_domain(url: str) -> str | None:
    """Extract the domain (netloc) from a URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain:
            return domain
        # Fallback: if no netloc (e.g. bare domain passed), try the path
        if parsed.path and "." in parsed.path:
            return parsed.path.split("/")[0]
    except Exception:
        pass
    return None


def build_favicon_url(domain: str) -> str:
    """Build the Google favicon service URL for a domain."""
    return FAVICON_TEMPLATE.format(domain=domain)


def build_icon_payload(domain: str) -> dict:
    """Build the Notion icon payload — custom emoji for GitHub, favicon for everything else."""
    if "github.com" in domain:
        return {
            "icon": {
                "type": "custom_emoji",
                "custom_emoji": {"id": GITHUB_CUSTOM_EMOJI_ID},
            }
        }
    return {
        "icon": {
            "type": "external",
            "external": {"url": build_favicon_url(domain)},
        }
    }


def set_page_icon(headers: dict, page_id: str, domain: str) -> bool:
    """Set the icon on a Notion page based on domain."""
    payload = build_icon_payload(domain)
    resp = requests.patch(
        f"{NOTION_BASE_URL}/pages/{page_id}",
        headers=headers,
        json=payload,
    )
    if resp.status_code == 429:
        # Rate limited — wait and retry once
        retry_after = int(resp.headers.get("Retry-After", 2))
        print(f"    ⏳ Rate limited, waiting {retry_after}s...")
        time.sleep(retry_after)
        resp = requests.patch(
            f"{NOTION_BASE_URL}/pages/{page_id}",
            headers=headers,
            json=payload,
        )
    return resp.status_code == 200


def main():
    parser = argparse.ArgumentParser(
        description="Backfill bookmark favicons in Notion"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview changes without applying"
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Limit number of bookmarks to process"
    )
    parser.add_argument(
        "--token", type=str, default=None, help="Notion API token (or set NOTION_API_KEY env var)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Rewrite icons even if already set"
    )
    args = parser.parse_args()

    token = args.token or os.environ.get("NOTION_API_KEY")
    if not token:
        print("❌ No Notion API token. Set NOTION_API_KEY or pass --token.")
        sys.exit(1)

    headers = get_notion_headers(token)

    print("📚 Fetching all bookmarks from Notion...")
    bookmarks = fetch_all_bookmarks(headers)
    print(f"✅ Found {len(bookmarks)} total bookmarks.\n")

    # Filter to those needing favicons
    force_rewrite = args.force
    to_process = []
    for bm in bookmarks:
        icon = bm.get("icon")
        url = bm.get("properties", {}).get("URL", {}).get("url")

        if icon is not None and not force_rewrite:
            continue  # Already has an icon
        if not url:
            continue  # No URL to derive favicon from

        domain = extract_domain(url)
        if not domain:
            desc = bm.get("properties", {}).get("Description", {}).get("title", [{}])
            title = desc[0].get("plain_text", "Unknown") if desc else "Unknown"
            print(f"  ⚠️ Skipping (bad URL): {title} — {url}")
            continue

        to_process.append({
            "id": bm["id"],
            "url": url,
            "domain": domain,
            "title": (
                bm.get("properties", {})
                .get("Description", {})
                .get("title", [{}])[0]
                .get("plain_text", "Unknown")
                if bm.get("properties", {}).get("Description", {}).get("title")
                else "Unknown"
            ),
        })

    if args.limit > 0:
        to_process = to_process[: args.limit]

    print(f"🔧 {len(to_process)} bookmarks need favicon icons.\n")

    if args.dry_run:
        print("🏃 DRY RUN — no changes will be made.\n")
        for item in to_process:
            favicon = build_favicon_url(item["domain"])
            print(f"  Would set: {item['title'][:50]} → {item['domain']}")
        print(f"\n✅ Dry run complete. {len(to_process)} bookmarks would be updated.")
        return

    # Process bookmarks
    success = 0
    failed = 0

    for i, item in enumerate(to_process, 1):
        ok = set_page_icon(headers, item["id"], item["domain"])
        icon_type = "🐙 github-light" if "github.com" in item["domain"] else "🌐 favicon"

        if ok:
            success += 1
            print(f"  ✅ [{i}/{len(to_process)}] {item['title'][:50]} → {item['domain']} ({icon_type})")
        else:
            failed += 1
            print(f"  ❌ [{i}/{len(to_process)}] FAILED: {item['title'][:50]} — {item['url']}")

        time.sleep(REQUEST_DELAY)

    print(f"\n{'='*50}")
    print(f"✅ Done! {success} updated, {failed} failed out of {len(to_process)} total.")


if __name__ == "__main__":
    main()
