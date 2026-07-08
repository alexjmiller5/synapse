#!/usr/bin/env python
"""Second-pass fixups for movies the main backfill flagged (typos/formatting/
subtitle/Spanish). Curated: each row is (notion_title, tmdb_query, rename_to).

- tmdb_query: the corrected string to search TMDB with (fixes the typo).
- rename_to:  new Notion Title (canonical), or None to keep Alex's title as-is.

Applies the matched film's genres/director/cast; renames the page when rename_to
is set. DRY-RUN by default; --apply to write.

Run: op run --env-file=.env.tpl -- uv run scripts/backfill_fixups.py [--apply]
"""

import os
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.external_data import map_genres, tmdb_details, tmdb_search  # noqa: E402

MOVIES_DS = "4eb907d5-1be3-41e3-be31-9afd33510a1f"
NOTION = "https://api.notion.com/v1"


def _opt(name):
    """Notion select/multi_select option names can't contain commas (API 400)."""
    return str(name).replace(",", "")
HEADERS = {
    "Authorization": f"Bearer {os.environ['NOTION_INTEGRATION_TOKEN']}",
    "Notion-Version": "2026-03-11",
    "Content-Type": "application/json",
}

# (current Notion title, corrected TMDB search query, rename Notion title to -> or None)
FIXUPS = [
    # --- typos: fix the title + apply ---
    ("Matila", "Matilda", "Matilda"),
    ("Westside Story", "West Side Story", "West Side Story"),
    ("Seven", "Se7en", "Se7en"),
    ("Dumb and Dumber Too", "Dumb and Dumber To", "Dumb and Dumber To"),
    ("Along Came Poly", "Along Came Polly", "Along Came Polly"),
    ("Mohallen Drive", "Mulholland Drive", "Mulholland Drive"),
    ("Final Destination Bloodines", "Final Destination Bloodlines", "Final Destination Bloodlines"),
    ("Jurrqsic World Rebirth", "Jurassic World Rebirth", "Jurassic World Rebirth"),
    ("Pirates of the Carribean: Dead Man’s Chest", "Pirates of the Caribbean Dead Man's Chest",
     "Pirates of the Caribbean: Dead Man's Chest"),
    ("Pirates of the Carribean: The Curse of the Black Pearl",
     "Pirates of the Caribbean The Curse of the Black Pearl",
     "Pirates of the Caribbean: The Curse of the Black Pearl"),
    ("King of Staton Island", "The King of Staten Island", "The King of Staten Island"),
    ("Chappanquiddick", "Chappaquiddick", "Chappaquiddick"),
    ("In the Line or Fire", "In the Line of Fire", "In the Line of Fire"),
    ("Erin Brokovich", "Erin Brockovich", "Erin Brockovich"),
    ("One Night at the Roxbury", "A Night at the Roxbury", "A Night at the Roxbury"),
    ("Blackphone", "The Black Phone", "The Black Phone"),
    ("50 Shades of Grey", "Fifty Shades of Grey", "Fifty Shades of Grey"),
    ("50 Shades Darker", "Fifty Shades Darker", "Fifty Shades Darker"),
    ("Creed 2", "Creed II", "Creed II"),
    ("Marley and Me", "Marley & Me", "Marley & Me"),
    ("Avatar 2", "Avatar: The Way of Water", "Avatar: The Way of Water"),
    ("Sing 1", "Sing", "Sing"),
    # --- wrong-match fixes: keep Alex's (common) title, search the right film ---
    ("Christmas Vacation", "National Lampoon's Christmas Vacation", None),
    ("Van Wilder", "National Lampoon's Van Wilder", None),
    # --- Alex's later calls ---
    ("As Good As They Come", "As Good as It Gets", "As Good as It Gets"),
    ("Kidnapped: The Elizabeth Smart Story", "The Elizabeth Smart Story", None),
    ("Abercrombie & Fitch: The Rise and Fall", "White Hot Abercrombie", None),
    ("Marty Supreme", "Marty Supreme", None),  # comma-in-cast fix (Tyler, the Creator)
    # --- formatting: normalize to canonical + apply ---
    ("The Whole 9 Yards", "The Whole Nine Yards", "The Whole Nine Yards"),
    ("3 Identical Strangers", "Three Identical Strangers", "Three Identical Strangers"),
    ("Gladiator 2", "Gladiator II", "Gladiator II"),
    ("Queen and Slim", "Queen & Slim", "Queen & Slim"),
    ("Thelma and Louise", "Thelma & Louise", "Thelma & Louise"),
    ("Minions: Rise of Gru", "Minions: The Rise of Gru", "Minions: The Rise of Gru"),
    ("Zombieland 2", "Zombieland: Double Tap", "Zombieland: Double Tap"),
    ("Operation Varsity Blues: College Admissions Scandal",
     "Operation Varsity Blues", "Operation Varsity Blues: The College Admissions Scandal"),
    # --- subtitle expansions / correct matches: keep Alex's title, apply metadata ---
    ("Pirates of the Caribbean", "Pirates of the Caribbean The Curse of the Black Pearl", None),
    ("Dear Zachary", "Dear Zachary A Letter to a Son About His Father", None),
    ("Super Size Me 2", "Super Size Me 2 Holy Chicken", None),
    ("The Green Book", "The Green Book Guide to Freedom", None),  # Alex: it's the documentary
    ("Spinal Tap", "This Is Spinal Tap", None),
    ("Borat", "Borat Cultural Learnings of America", None),
    ("Kill Bill", "Kill Bill Vol 1", None),
    ("Dr. Strangelove", "Dr. Strangelove", None),
    ("Final Destination 4", "The Final Destination", None),
    # --- Spanish-language: keep Alex's Spanish title, apply the film's metadata ---
    ("Mujeres al borde de un ataque nervios", "Mujeres al borde de un ataque de nervios", None),
    ("Carne trémula", "Live Flesh", None),
    ("La lengua de las mariposas", "La lengua de las mariposas", None),
    ("Dos Cataluñas", "Two Catalonias", None),
    ("Isla mínima", "Marshland", None),
    ("Te doy mis ojos", "Take My Eyes", None),
    ("Los siete virgenes", "7 vírgenes", None),
]


def genre_options():
    r = requests.get(f"{NOTION}/data_sources/{MOVIES_DS}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return [o["name"] for o in r.json()["properties"]["Genres"]["multi_select"]["options"]]


def find_page(title):
    r = requests.post(
        f"{NOTION}/data_sources/{MOVIES_DS}/query",
        headers=HEADERS,
        json={"filter": {"property": "Title", "title": {"equals": title}}, "page_size": 1},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()["results"]
    return res[0]["id"] if res else None


def main():
    apply = "--apply" in sys.argv
    options = genre_options()
    key = os.environ["TMDB_API_KEY"]
    print(f"{'APPLYING' if apply else 'DRY-RUN'} — {len(FIXUPS)} fixups\n")

    ok = missing_page = no_tmdb = 0
    for notion_title, query, rename_to in FIXUPS:
        pid = find_page(notion_title)
        if not pid:
            print(f"  ? PAGE NOT FOUND: {notion_title!r}")
            missing_page += 1
            continue
        results = tmdb_search("movie", query, key)
        if not results:
            print(f"  ✗ NO TMDB for query {query!r} ({notion_title!r})")
            no_tmdb += 1
            continue
        top = results[0]
        meta = tmdb_details("movie", top["id"], key)
        props = {}
        if meta["genres"]:
            props["Genres"] = {
                "multi_select": [{"name": _opt(g)} for g in map_genres(meta["genres"], options)]
            }
        if meta["director"]:
            props["Director"] = {"select": {"name": _opt(meta["director"])}}
        if meta["cast"]:
            props["Famous Cast Members"] = {"multi_select": [{"name": _opt(c)} for c in meta["cast"]]}
        if rename_to:
            props["Title"] = {"title": [{"text": {"content": rename_to}}]}

        newname = f" [rename -> {rename_to!r}]" if rename_to else ""
        print(
            f"  ✓ {notion_title!r} -> {top['title']} ({top['year']}) "
            f"| dir={meta['director'] or '—'}{newname}"
        )
        if apply:
            resp = requests.patch(
                f"{NOTION}/pages/{pid}", headers=HEADERS, json={"properties": props}, timeout=30
            )
            if resp.status_code >= 300:
                print(f"      ❌ write failed: {resp.status_code} {resp.text[:120]}")
                continue
        ok += 1

    print(f"\n=== {'applied' if apply else 'would apply'}: {ok} | "
          f"page-not-found: {missing_page} | no-tmdb: {no_tmdb}")


if __name__ == "__main__":
    main()
