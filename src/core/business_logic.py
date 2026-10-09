import re

from core.config import CATEGORIES
from core.timeutils import today_eastern
from core.handlers import (
    handle_hub_logic,
    handle_url_media,
    handle_youtube_logic,
    handle_movies_tv_logic,
)
from core.soma_hub import pull_rows


# Common "UTF-8 decoded as CP1252/Latin-1" mojibake → the character it should be.
# Ordered longest/most-specific first so a 3-char sequence is matched before the
# 2-char NBSP one. Only multi-char artifact sequences are replaced — a bare
# accented letter (é in "Sérgio", â in French "âme") never matches, so accents
# are preserved. Escapes used so the source stays ASCII and unambiguous.
_MOJIBAKE_MAP = [
    ("\u00e2\u20ac\u2122", "'"),  # UTF-8 U+2019 (') decoded as CP1252
    ("\u00e2\u20ac\u02dc", "'"),  # U+2018 (') left single quote
    ("\u00e2\u20ac\u0153", '"'),  # U+201C (") left double quote
    ("\u00e2\u20ac\u009d", '"'),  # U+201D (") right double quote
    ("\u00e2\u20ac\u201d", "\u2014"),  # U+2014 em dash (CP1252 0x94)
    ("\u00e2\u20ac\u0094", "\u2014"),  # U+2014 em dash (latin-1 0x94)
    ("\u00e2\u20ac\u0093", "\u2013"),  # U+2013 en dash
    ("\u00c2\u00a0", " "),  # mojibake of a NBSP
    ("\ufeff", ""),  # stray BOM
    ("\u00a0", " "),  # bare NBSP -> normal space
]


def clean_text(s):
    """Deterministically de-spam and de-mojibake an extracted string.

    Applied to a task's verbatim name so obvious AI/paste junk (newline spam,
    repeated punctuation, encoding artifacts) never reaches the row. Conservative and idempotent: only collapses clearly-spammy repeats
    and a fixed set of mojibake sequences; never ASCII-folds accents. Non-str
    input passes through untouched.
    """
    if not isinstance(s, str):
        return s
    for bad, good in _MOJIBAKE_MAP:
        s = s.replace(bad, good)
    s = re.sub(r"\n{3,}", "\n\n", s)  # newline spam -> blank line
    s = re.sub(r"—{2,}", "—", s)  # em-dash spam -> one em dash
    s = re.sub(r"\.{3,}", "…", s)  # 3+ dots -> single ellipsis
    s = re.sub(r"!{3,}", "!", s)  # !!! spam -> one
    s = re.sub(r"\?{3,}", "?", s)  # ??? spam -> one
    return s.strip()


def fetch_inventory_map(category):
    """{item name: row id} for a hub-backed category, from the hub's live rows.

    The names go into the extraction prompt so a capture reuses an existing
    spelling; the handler matches on the same column when it writes.
    """
    print(f"📚 Fetching full inventory for {category}...")
    stanza = CATEGORIES[category]
    key = stanza["match_on"]
    try:
        rows = pull_rows(stanza["table"], [key])
    except Exception as e:
        print(f"   ⚠️ Inventory unavailable: {e}")
        return {}
    inventory = {r[key]: r["id"] for r in rows if r.get(key)}
    print(f"   ✅ Loaded {len(inventory)} items.")
    return inventory


def fetch_active_projects():
    """Active projects from the workspace's workflow.projects table, as
    (prompt list of names, {name: row id}); none when it binds no table."""
    from core.workflow import active_projects, binding_for

    binding = binding_for("projects")
    return active_projects(binding) if binding is not None else ([], {})


def apply_business_logic(category, data, related_project=None, source_text=None):
    today_str = today_eastern().isoformat()

    if category == "tasks":
        data["Status"] = "To Do"
        # Tasks default to High priority (project-routed tasks included) —
        # the AI usually sets this, but never rely on it.
        data.setdefault("Priority", "High")
        # Grounding guard: a task Name MUST be the user's verbatim text
        # (prompts.yaml says so), but the AI sometimes rewrites/hallucinates it.
        # Force it back to the original input. ONLY tasks — other categories
        # legitimately transform their title (groceries Title-Cases, movies
        # correct titles).
        if source_text is not None:
            data["Name"] = clean_text(source_text)
        # Place-tagged tasks (workspace tasks.place_tags) are dateless by design: the
        # prompt returns "" when no date was given — drop it so an empty date
        # never reaches the row.
        if not data.get("Due Date"):
            data.pop("Due Date", None)
        if related_project:
            annotation = f"Project: {related_project}"
            existing = data.get("Notes") or ""
            data["Notes"] = f"{existing}\n\n{annotation}" if existing else annotation

    elif category == "podcasts":
        if data.get("Status") == "Finished":
            data["Date Listened To"] = today_str

    elif category == "bookmarks":
        # House style the catalog enforces: no trailing period, Github tag on
        # github.com urls - applied here so the row is valid before the push.
        if isinstance(data.get("Description"), str):
            data["Description"] = data["Description"].rstrip(".")
        if "github.com" in data.get("URL", ""):
            tags = data.get("Tags", [])
            if isinstance(tags, list) and "Github" not in tags:
                tags.append("Github")
                data["Tags"] = tags

    return data


LOGIC_HANDLERS = {
    "youtube-videos": handle_youtube_logic,
    "movies": handle_movies_tv_logic,
    "tv-shows": handle_movies_tv_logic,
}


def execute_logic(category, data, inventory_map=None):
    from core.workflow import create_task

    if category == "tasks":
        return create_task(data)
    print(f"⚙️ Executing Logic for: {category}")
    if category in ("podcasts", "articles"):
        return handle_url_media(category, data)
    if "columns" in CATEGORIES[category]:
        return handle_hub_logic(category, data)
    return LOGIC_HANDLERS[category](category, data)
