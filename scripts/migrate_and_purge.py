#!/usr/bin/env python
"""Move misfiled TV/docuseries out of the Movies DB into the TV Shows DB (with
TMDB TV metadata), and trash the ambiguous entries Alex said to delete.

DRY-RUN by default; --apply to write.
Run: op run --env-file=.env.tpl -- uv run scripts/migrate_and_purge.py [--apply]
"""

import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.external_data import map_genres, tmdb_details, tmdb_search  # noqa: E402

MOVIES_DS = "4eb907d5-1be3-41e3-be31-9afd33510a1f"
TV_DS = "507e4205-3476-4d19-9e50-584c9ee96c49"
NOTION = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {os.environ['NOTION_INTEGRATION_TOKEN']}",
    "Notion-Version": "2026-03-11",
    "Content-Type": "application/json",
}

# Misfiled: (movies-DB title, TMDB TV search query)
MIGRATE = [
    ("Fleabag", "Fleabag"),
    ("30 Rock", "30 Rock"),
    ("The Newsroom", "The Newsroom"),
    ("Monty Python's Flying Circus", "Monty Python's Flying Circus"),
    ("Mind Hunter", "Mindhunter"),
    ("Our Planet", "Our Planet"),
    ("Sean Combs: The Reckoning", "Sean Combs The Reckoning"),
    ("Billy Joel: And So It Goes", "Billy Joel And So It Goes"),
    ("Trainwreck: The Cukt of American Aparrel", "Trainwreck The Cult of American Apparel"),
    ("Afghanistan’s Dancing Boys", "Afghanistan's Dancing Boys"),
]

# Ambiguous — Alex said delete
DELETE = [
    "Tennage Mutant Ninja Turtles",
    "Rodney Dangerfield",
    "Charlie Chaplin Collection",
    "Waiting In Vain",
]


def tv_genre_options():
    r = requests.get(f"{NOTION}/data_sources/{TV_DS}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return [o["name"] for o in r.json()["properties"]["Genres"]["multi_select"]["options"]]


def find_movie(title):
    r = requests.post(
        f"{NOTION}/data_sources/{MOVIES_DS}/query",
        headers=HEADERS,
        json={"filter": {"property": "Title", "title": {"equals": title}}, "page_size": 1},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()["results"]
    return res[0] if res else None


def trash(pid):
    requests.patch(f"{NOTION}/pages/{pid}", headers=HEADERS, json={"in_trash": True}, timeout=30)


def main():
    apply = "--apply" in sys.argv
    options = tv_genre_options()
    key = os.environ["TMDB_API_KEY"]
    print(f"{'APPLYING' if apply else 'DRY-RUN'}\n")

    print("== MIGRATE (movies -> TV) ==")
    migrated = 0
    for movie_title, query in MIGRATE:
        page = find_movie(movie_title)
        if not page:
            print(f"  ? not in Movies DB: {movie_title!r}")
            continue
        results = tmdb_search("tv", query, key)
        meta = (
            tmdb_details("tv", results[0]["id"], key)
            if results
            else {"genres": [], "director": "", "cast": []}
        )

        props = {"Title": {"title": [{"text": {"content": movie_title}}]}}
        # carry over the watched-state fields that safely transfer
        src = page["properties"]
        if src.get("Date Watched", {}).get("date"):
            props["Date Watched"] = {"date": src["Date Watched"]["date"]}
        if src.get("Tags", {}).get("multi_select"):
            props["Tags"] = {
                "multi_select": [{"name": t["name"]} for t in src["Tags"]["multi_select"]]
            }
        if meta["genres"]:
            props["Genres"] = {
                "multi_select": [
                    {"name": o.replace(",", "")} for o in map_genres(meta["genres"], options)
                ]
            }
        if meta["director"]:
            props["Director"] = {"select": {"name": meta["director"].replace(",", "")}}
        if meta["cast"]:
            props["Famous Cast Members"] = {
                "multi_select": [{"name": c.replace(",", "")} for c in meta["cast"]]
            }

        print(
            f"  ✓ {movie_title!r} -> TV | creator={meta['director'] or '—'} | "
            f"genres=[{', '.join(map_genres(meta['genres'], options)) if meta['genres'] else '—'}]"
        )
        if apply:
            resp = requests.post(
                f"{NOTION}/pages",
                headers=HEADERS,
                json={
                    "parent": {"type": "data_source_id", "data_source_id": TV_DS},
                    "properties": props,
                },
                timeout=30,
            )
            if resp.status_code >= 300:
                print(f"      ❌ TV create failed: {resp.status_code} {resp.text[:120]}")
                continue
            trash(page["id"])
        migrated += 1

    print("\n== DELETE (trash) ==")
    deleted = 0
    for title in DELETE:
        page = find_movie(title)
        if not page:
            print(f"  ? not found: {title!r}")
            continue
        print(f"  🗑  {title!r}")
        if apply:
            trash(page["id"])
        deleted += 1

    print(
        f"\n=== {'migrated' if apply else 'would migrate'}: {migrated} | "
        f"{'deleted' if apply else 'would delete'}: {deleted}"
    )


if __name__ == "__main__":
    main()
