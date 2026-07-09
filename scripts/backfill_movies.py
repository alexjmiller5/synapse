#!/usr/bin/env python
"""Backfill authoritative TMDB genres/director/cast onto existing Movies DB pages.

Reuses the live pipeline's TMDB logic (core.external_data). DRY-RUN by default —
prints planned changes and does NOT write. Pass --apply to write.

Match confidence guards against clobbering a curated title with the wrong film:
  HIGH   (normalized title exact match)        -> auto-applied
  MEDIUM (one title contains the other)        -> auto-applied
  LOW    (titles differ) / NO-MATCH            -> skipped + reported for manual review

Only fields TMDB actually returns are overwritten; Tags/Status/etc. are untouched.

Run:  op run --env-file=.env.tpl -- uv run scripts/backfill_movies.py [--apply] [--limit N]
"""

import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.external_data import map_genres, tmdb_details, tmdb_search  # noqa: E402

MOVIES_DS = "4eb907d5-1be3-41e3-be31-9afd33510a1f"
NOTION = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {os.environ['NOTION_INTEGRATION_TOKEN']}",
    "Notion-Version": "2026-03-11",
    "Content-Type": "application/json",
}

_ARTICLE = re.compile(r"^(the|a|an)\s+")


def norm(s):
    s = (s or "").lower().strip()
    s = _ARTICLE.sub("", s)
    return re.sub(r"[^a-z0-9]", "", s)


_YEAR_SUFFIX = re.compile(r"\s*\((\d{4})\)\s*$")


def pick_tmdb(movie_title, tmdb_key):
    """Return (meta, confidence) or (None, 'NO-MATCH').

    Convention (Alex's Movies DB): a bare title means the version people KNOW, so
    among exact title matches we take the most-rated (highest vote_count) — that's
    the well-known one (Ghostbusters->1984, Scary Movie->2000). A '(YYYY)' suffix
    pins that specific year.
    """
    m = _YEAR_SUFFIX.search(movie_title)
    want_year = m.group(1) if m else None
    query = _YEAR_SUFFIX.sub("", movie_title).strip() if m else movie_title

    results = tmdb_search("movie", query, tmdb_key)
    if not results:
        return None, "NO-MATCH"

    exact = [r for r in results if norm(r["title"]) == norm(query)]
    if want_year:
        pool = exact or results
        chosen = next((r for r in pool if r["year"] == want_year), None)
        conf = "HIGH" if chosen else "MEDIUM"
        chosen = chosen or pool[0]
    elif exact:
        chosen = max(exact, key=lambda r: r["votes"])  # most-rated = the known one
        conf = "HIGH"
    else:
        chosen = results[0]  # popularity top; titles differ -> low confidence
        a, b = norm(query), norm(chosen["title"])
        conf = "MEDIUM" if (a in b or b in a) else "LOW"

    if conf == "LOW":
        return {"matched_title": chosen["title"], "year": chosen["year"]}, "LOW"

    meta = tmdb_details("movie", chosen["id"], tmdb_key)
    meta["matched_title"] = chosen["title"]
    meta["year"] = chosen["year"]
    return meta, conf


def all_movies():
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(
            f"{NOTION}/data_sources/{MOVIES_DS}/query", headers=HEADERS, json=body, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        yield from data["results"]
        if not data.get("has_more"):
            return
        cursor = data["next_cursor"]


def genre_options():
    resp = requests.get(f"{NOTION}/data_sources/{MOVIES_DS}", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return [o["name"] for o in resp.json()["properties"]["Genres"]["multi_select"]["options"]]


def title_of(page):
    t = page["properties"]["Title"]["title"]
    return t[0]["plain_text"] if t else ""


def build_update(meta, existing_genres):
    """Only include fields TMDB actually returned (never wipe with blanks)."""
    props = {}
    if meta["genres"]:
        props["Genres"] = {
            "multi_select": [{"name": g} for g in map_genres(meta["genres"], existing_genres)]
        }
    if meta["director"]:
        props["Director"] = {"select": {"name": meta["director"]}}
    if meta["cast"]:
        props["Famous Cast Members"] = {"multi_select": [{"name": c} for c in meta["cast"]]}
    return props


def main():
    apply = "--apply" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    tmdb_key = os.environ["TMDB_API_KEY"]
    options = genre_options()
    print(f"{'APPLYING' if apply else 'DRY-RUN'} — Genres allowlist has {len(options)} options\n")

    updated = skipped_low = no_match = 0
    review = []
    for i, page in enumerate(all_movies()):
        if limit and i >= limit:
            break
        title = title_of(page)
        if not title:
            continue
        meta, conf = pick_tmdb(title, tmdb_key)
        if conf == "NO-MATCH":
            no_match += 1
            review.append((title, "NO-MATCH", ""))
            print(f"  ✗ NO-MATCH   {title}")
            continue

        matched = f"{meta['matched_title']} ({meta['year']})"
        # Only exact-title (HIGH) matches auto-apply. MEDIUM (containment) is too
        # loose — it silently swaps sequels/docs — so it goes to manual review.
        if conf in ("LOW", "MEDIUM"):
            skipped_low += 1
            review.append((title, conf, matched))
            print(f"  ⚠ {conf}   {title!r} -> {matched} — SKIPPED (review)")
            continue

        props = build_update(meta, options)
        if not props:
            continue
        d = meta["director"] or "—"
        g = ", ".join(map_genres(meta["genres"], options)) if meta["genres"] else "—"
        print(f"  ✓ {conf:6} {title!r} -> {matched} | dir={d} | genres=[{g}]")
        if apply:
            resp = requests.patch(
                f"{NOTION}/pages/{page['id']}",
                headers=HEADERS,
                json={"properties": props},
                timeout=30,
            )
            if resp.status_code >= 300:
                print(f"      ❌ write failed: {resp.status_code} {resp.text[:120]}")
                continue
        updated += 1
        time.sleep(0.3)  # Notion ~3 req/s; TMDB fine at this pace

    print(
        f"\n=== {'applied' if apply else 'would apply'}: {updated} | "
        f"low-confidence skipped: {skipped_low} | no-match: {no_match}"
    )
    if review:
        print("\nNeeds manual review:")
        for t, why, matched in review:
            print(f"  [{why}] {t}" + (f"  (TMDB top: {matched})" if matched else ""))


if __name__ == "__main__":
    main()
