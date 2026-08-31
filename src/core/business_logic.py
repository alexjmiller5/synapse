import re

from core.config import DATABASES
from core.secrets import get_db_id
from core.clients import get_notion
from core.timeutils import today_eastern
from core.notion_utils import clean_text, prop_id
from core.external_data import get_tmdb_metadata, map_genres
from core.handlers import (
    handle_groceries_fun_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_bookmarks_logic,
    handle_people_logic,
    handle_bucket_list_logic,
    handle_places_logic,
    handle_default_logic,
)


def query_notion_db(category_key, query_body=None):
    """Generic helper to safely query a Notion database."""
    db_id = get_db_id(category_key)
    if not get_notion() or not db_id:
        return []

    if query_body is None:
        query_body = {"page_size": 100}

    try:
        resp = get_notion().request(path=f"databases/{db_id}/query", method="POST", body=query_body)
        return resp.get("results", [])
    except Exception as e:
        print(f"❌ Failed to query {category_key}: {e}")
        return []


def fetch_inventory_map(category):
    """
    Fetches ALL pages from a DB and returns a dict: {'Item Name': 'Page ID'}
    Uses raw .request() to bypass missing SDK methods.
    """
    print(f"📚 Fetching full inventory for {category}...")
    results = query_notion_db(category)

    inventory = {}
    for page in results:
        try:
            title_prop = page["properties"].get("Name", {}).get("title", [])
            if title_prop:
                name = title_prop[0]["plain_text"]
                inventory[name] = page["id"]
        except Exception:
            continue

    print(f"   ✅ Loaded {len(inventory)} items.")
    return inventory


# databases.yaml property type -> the Notion property type it maps to (for validation).
_YAML_TO_NOTION_TYPE = {
    "title": "title",
    "rich_text": "rich_text",
    "rich_text_list": "rich_text",
    "select": "select",
    "multi_select": "multi_select",
    "status": "status",
    "date": "date",
    "url": "url",
    "relation": "relation",
    "checkbox": "checkbox",
}


def _options_from_prop(prop):
    """Option names for a live select/multi_select/status property (else [])."""
    t = prop.get("type")
    if t in ("select", "multi_select", "status"):
        return [o["name"] for o in prop.get(t, {}).get("options", [])]
    return []


def fetch_db_schema(db_id):
    """Live property schema {name: prop_object} for a DB — one Notion call.
    Returns {} on no-client / no-id / error."""
    if not get_notion() or not db_id:
        return {}
    try:
        return get_notion().databases.retrieve(db_id).get("properties", {})
    except Exception as e:
        print(f"   ❌ Schema fetch failed for {db_id}: {e}")
        return {}


def _find_prop(schema, category, prop_name):
    """Locate a property in a live schema by stable id (rename-safe), name fallback."""
    target_id = prop_id(category, prop_name) if category else prop_name
    return next((p for p in schema.values() if p.get("id") == target_id), None) or schema.get(
        prop_name
    )


def fetch_property_options(db_id, prop_name, category=None, schema=None):
    """Live options for a select/status/multi_select property. Pass a pre-fetched
    schema to avoid a redundant retrieve (hydration fetches once per category)."""
    if schema is None:
        schema = fetch_db_schema(db_id)
    prop = _find_prop(schema, category, prop_name)
    return _options_from_prop(prop) if prop else []


def validate_category(category, details, schema):
    """Drift issues between a category's databases.yaml stanza and the live Notion
    schema: missing properties, type mismatches, allowlist options not in Notion."""
    issues = []
    if not schema:
        return [f"{category}: could not fetch live schema"]
    for name, rules in details.get("properties", {}).items():
        ytype = rules.get("type")
        if ytype == "ignore":
            continue
        live = _find_prop(schema, category, name)
        if not live:
            issues.append(f"{category}.{name}: not found in Notion (id={prop_id(category, name)})")
            continue
        expected = _YAML_TO_NOTION_TYPE.get(ytype, ytype)
        if live.get("type") != expected:
            issues.append(
                f"{category}.{name}: Notion type '{live.get('type')}' != expected '{expected}'"
            )
        allowlist = rules.get("allowlist")
        if allowlist:
            missing = [o for o in allowlist if o not in _options_from_prop(live)]
            if missing:
                issues.append(
                    f"{category}.{name}: allowlist options not in Notion select: {missing}"
                )
    return issues


def validate_all():
    """Validate EVERY (non-helper) category's databases.yaml against live Notion.
    Returns {category: [issues]} for categories with drift (empty dict = all good)."""
    report = {}
    for category, details in DATABASES.get("databases", {}).items():
        if details.get("helper"):
            continue
        db_id = get_db_id(category)
        if not db_id:
            report[category] = ["no db_id configured"]
            continue
        issues = validate_category(category, details, fetch_db_schema(db_id))
        if issues:
            report[category] = issues
    return report


def hydrate_dynamic_options(only_category=None):
    """Load live Notion select/status options into each category's schema AND
    validate that category against the live structure (free — same schema fetch).

    Pass only_category to hydrate a SINGLE category (the classified one) — the hot
    path. One `databases.retrieve` per category (not per property).
    """
    print(f"🔄 Hydrating Options{f' for {only_category}' if only_category else ''}...")
    for category, details in DATABASES.get("databases", {}).items():
        if only_category and category != only_category:
            continue
        if details.get("helper"):
            continue
        db_id = get_db_id(category)
        if not db_id:
            print(f"   ⚠️ Skipping {category} (No DB ID)")
            continue

        schema = fetch_db_schema(db_id)  # one fetch, reused for hydrate + validate

        # Per-execution config-drift check (databases.yaml vs live Notion).
        for issue in validate_category(category, details, schema):
            print(f"   ⚠️ VALIDATE: {issue}")

        for prop_name, rules in details.get("properties", {}).items():
            if rules.get("type") not in ["select", "multi_select", "status"]:
                continue

            real_options = fetch_property_options(db_id, prop_name, category, schema=schema)
            allowlist = rules.get("allowlist")
            final_options = (
                [opt for opt in real_options if opt in allowlist] if allowlist else real_options
            )

            rules["_runtime_options"] = final_options
            print(
                f"   🔹 {category} [{prop_name}]: Loaded {len(final_options)} options: {final_options}"
            )
    print("✅ Hydration complete.")


def fetch_trips_inventory():
    """
    Fetches Trips sorted by date.
    Returns:
      - inventory_text: ["Trip Name (Date: 2025-01-01)", ...]
      - id_map: {"Trip Name": "page-id"}
    """
    print("✈️ Fetching Trips Inventory...")

    # Specific query: Sort by 'Dates' descending
    query_body = {"sorts": [{"property": "Dates", "direction": "descending"}]}

    results = query_notion_db("trips", query_body)

    id_map = {}
    inventory_text = []

    for page in results:
        try:
            # Extract Name
            title_prop = page["properties"].get("Name", {}).get("title", [])
            name = title_prop[0]["plain_text"] if title_prop else "Untitled"

            # Extract Date
            date_prop = page["properties"].get("Dates", {}).get("date", {})
            date_str = date_prop.get("start", "No Date") if date_prop else "No Date"

            # 1. Map uses STRICT Name
            id_map[name] = page["id"]

            # 2. Prompt gets Name + Date (So AI can distinguish old vs new)
            inventory_text.append(f"{name} (Date: {date_str})")

        except Exception:
            continue

    print(f"   ✅ Loaded {len(inventory_text)} trips.")
    return inventory_text, id_map


def fetch_active_projects():
    """
    Fetches active projects from the dedicated Projects database.
    Returns:
    - prompt_list: ["Project Name", ...]
    - id_map: {"Project Name": "page-id"}
    """
    print("📂 Fetching active projects from Projects DB...")

    query_body = {
        "filter": {
            "or": [
                {"property": "Status", "status": {"equals": "To Do"}},
                {"property": "Status", "status": {"equals": "In progress"}},
            ]
        },
        "page_size": 100,
    }

    results = query_notion_db("projects", query_body)

    prompt_list = []
    id_map = {}

    for p in results:
        try:
            props = p["properties"]
            page_id = p["id"]

            # Extract project title
            title_prop = props.get("Title", {}).get("title", [])
            name = title_prop[0]["plain_text"] if title_prop else "Unknown"

            if name == "Unknown":
                continue

            id_map[name] = page_id
            prompt_list.append(name)

            print(f"   👉 Loaded Project: '{name}' (ID: {page_id})")

        except Exception as e:
            print(f"   ⚠️ Skipping a project due to error: {e}")
            continue

    print(f"✅ Total Active Projects Loaded: {len(prompt_list)}")
    return prompt_list, id_map


def _enrich_from_tmdb(category, data, kind):
    """Override the AI's guessed Genres/Director/Famous Cast Members with
    authoritative TMDB data for a movie/TV title.

    On no key / no match / any error, get_tmdb_metadata returns None and we
    flag `_tmdb_failed` so the pipeline creates a low-prior "fix metadata" chore
    linked to the new page (instead of silently trusting the AI's guesses).
    Each field is overridden only when TMDB actually supplies a value.
    """
    title = data.get("Title")
    if not title:
        return
    meta = get_tmdb_metadata(title, kind)
    if not meta:
        data["_tmdb_failed"] = True  # non-schema flag; build_notion_properties ignores it
        return

    # Adopt TMDB's canonical title so the page matches the metadata written for it
    # (the AI titled "disclosure day" as "Disclosure"; TMDB matched "Disclosure Day").
    # Skip when the AI deliberately year-disambiguated ("Ghostbusters (2016)") —
    # the bare matched title would collide with the original in dedupe.
    if meta.get("matched_title") and not re.search(r"\(\d{4}\)", title):
        data["Title"] = meta["matched_title"]

    if meta["genres"]:
        # Map to Alex's existing Notion 'Genres' options (hydrated onto the
        # property as _runtime_options); unknown genres pass through and
        # multi_select auto-creates them on write.
        existing = (
            DATABASES.get("databases", {})
            .get(category, {})
            .get("properties", {})
            .get("Genres", {})
            .get("_runtime_options", [])
        )
        data["Genres"] = map_genres(meta["genres"], existing)
    if meta["director"]:
        data["Director"] = meta["director"]
    if meta["cast"]:
        data["Famous Cast Members"] = meta["cast"]


def apply_business_logic(category, data, related_project=None, source_text=None):
    today_str = today_eastern().isoformat()

    if category == "tasks":
        data["Status"] = "To Do"
        # Tasks default to High priority (project-routed tasks included) —
        # the AI usually sets this, but never rely on it.
        data.setdefault("Priority", "High")
        # Grounding guard: a task Name MUST be the user's verbatim text
        # (databases.yaml says so), but the AI sometimes rewrites/hallucinates it.
        # Force it back to the original input. ONLY tasks — other categories
        # legitimately transform their title (groceries Title-Cases, movies
        # correct titles). clean_text also runs at the write choke-point; applied
        # here too so the grounded value is clean wherever data is read.
        if source_text is not None:
            data["Name"] = clean_text(source_text)
        # Place-tagged tasks (NOTION_TASKS_PLACE_TAGS) are dateless by design: the
        # prompt returns "" when no date was given — drop it so an empty date
        # payload never reaches Notion.
        if not data.get("Due Date"):
            data.pop("Due Date", None)
        if related_project:
            data["Notes"] = f"Project: {related_project}"

    elif category == "movies":
        if "Status" not in data:
            data["Status"] = "Not Started"
        _enrich_from_tmdb(category, data, "movie")

    elif category == "tv-shows":
        _enrich_from_tmdb(category, data, "tv")

    elif category == "podcasts":
        if data.get("Status") == "Finished":
            data["Date Listened To"] = today_str

    elif category == "youtube-videos":
        if data.get("Status") == "Watched":
            data["Date Watched"] = today_str

    elif category == "bookmarks":
        if "github.com" in data.get("URL", ""):
            tags = data.get("Tags", [])
            if isinstance(tags, list) and "Github" not in tags:
                tags.append("Github")
                data["Tags"] = tags

    return data


LOGIC_HANDLERS = {
    "places": handle_places_logic,
    "groceries": handle_groceries_fun_logic,
    "fun-activities": handle_groceries_fun_logic,
    "youtube-videos": handle_youtube_logic,
    "movies": handle_movies_tv_logic,
    "tv-shows": handle_movies_tv_logic,
    "bookmarks": handle_bookmarks_logic,
    "people": handle_people_logic,
    "bucket-list": handle_bucket_list_logic,
}


def execute_logic(category, data, inventory_map=None, trips_id_map=None):
    print(f"⚙️ Executing Logic for: {category}")

    if category == "places":
        return handle_places_logic(category, data, trips_id_map)

    if category in ["groceries", "fun-activities"]:
        return handle_groceries_fun_logic(category, data, inventory_map)

    handler = LOGIC_HANDLERS.get(category, handle_default_logic)
    return handler(category, data)
