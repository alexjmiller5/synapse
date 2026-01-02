import json
from datetime import date
from config import DATABASES
from gcp_secrets import get_db_id
from clients import notion
from handlers import (
    handle_groceries_fun_logic,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_bookmarks_logic,
    handle_people_logic,
    handle_bucket_list_logic,
    handle_places_logic,
    handle_default_logic,
)


# ==========================================
# 3. DYNAMIC HYDRATION
# ==========================================
def query_notion_db(category_key, query_body=None):
    """Generic helper to safely query a Notion database."""
    db_id = get_db_id(category_key)
    if not notion or not db_id:
        return []

    # Default body if none provided
    if query_body is None:
        query_body = {"page_size": 100}

    try:
        resp = notion.request(
            path=f"databases/{db_id}/query", method="POST", body=query_body
        )
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
            # Safe title extraction
            title_prop = page["properties"].get("Name", {}).get("title", [])
            if title_prop:
                name = title_prop[0]["plain_text"]
                inventory[name] = page["id"]
        except Exception:
            continue

    print(f"   ✅ Loaded {len(inventory)} items.")
    return inventory


def fetch_property_options(db_id, prop_name):
    if not notion:
        return []
    try:
        # LOGGING ADDED: verifying the ID being passed to the SDK
        # print(f"🔍 Retrieving DB: {repr(db_id)}")
        db = notion.databases.retrieve(db_id)

        if "properties" not in db:
            print(f"   ❌ CRITICAL: Response missing 'properties' key.")
            print(f"   ❌ Object Type: {db.get('object')}")
            print(f"   ❌ RAW RESPONSE: {json.dumps(db, default=str)}")
            return []

        prop = db["properties"].get(prop_name)
        if not prop:
            return []

        prop_type = prop.get("type")
        if prop_type == "select":
            return [o["name"] for o in prop["select"]["options"]]
        if prop_type == "multi_select":
            return [o["name"] for o in prop["multi_select"]["options"]]
        if prop_type == "status":
            return [o["name"] for o in prop["status"]["options"]]
        return []
    except Exception as e:
        print(f"   ❌ Exception fetching '{prop_name}': {e}")
        return []


def hydrate_dynamic_options():
    print("🔄 Hydrating Options...")
    for category, details in DATABASES.get("databases", {}).items():
        if category in ["logs", "youtube-channels"]:
            continue
        db_id = get_db_id(category)
        if not db_id:
            print(f"   ⚠️ Skipping {category} (No DB ID)")
            continue

        for prop_name, rules in details.get("properties", {}).items():
            if rules.get("type") not in ["select", "multi_select", "status"]:
                continue

            real_options = fetch_property_options(db_id, prop_name)
            allowlist = rules.get("allowlist")

            # Logic: Use allowlist if present, else real options
            final_options = (
                [opt for opt in real_options if opt in allowlist]
                if allowlist
                else real_options
            )

            # Store back into CONFIG memory for Schema Generation
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
    print(f"✈️ Fetching Trips Inventory...")

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

            # 1. Map uses STRICT Name (Matches Groceries logic)
            id_map[name] = page["id"]

            # 2. Prompt gets Name + Date (So AI can distinguish old vs new)
            inventory_text.append(f"{name} (Date: {date_str})")

        except Exception:
            continue

    print(f"   ✅ Loaded {len(inventory_text)} trips.")
    return inventory_text, id_map


def fetch_active_projects():
    """
    Returns:
      - prompt_list: ["Project Name (Context: notes...)", ...]
      - id_map: {"Project Name": "page-id"}
    """
    # Specific query: Filter for active Projects (Tags contains 'Project', Status != 'Completed')
    query_body = {
        "filter": {
            "and": [
                {"property": "Tags", "multi_select": {"contains": "Project"}},
                {
                    "property": "Status",
                    "status": {"does_not_equal": "Completed"},
                },
            ]
        },
        "page_size": 100,
    }

    results = query_notion_db("tasks", query_body)

    prompt_list = []
    id_map = {}

    for p in results:
        try:
            props = p["properties"]
            page_id = p["id"]

            # Dynamic Name Lookup: Find the property of type 'title'
            name = "Unknown"
            for prop_data in props.values():
                if prop_data["type"] == "title" and prop_data.get("title"):
                    name = prop_data["title"][0]["plain_text"]
                    break

            if name == "Unknown":
                continue

            # Store ID for later
            id_map[name] = page_id

            # Context Building (Notes)
            notes_obj = props.get("Notes", {}).get("rich_text", [])
            notes_text = "".join([t["plain_text"] for t in notes_obj])
            clean_notes = notes_text.replace("\n", " ").strip()

            # Truncate long notes
            if len(clean_notes) > 150:
                clean_notes = clean_notes[:150] + "..."

            entry = f"{name} (Context: {clean_notes})" if clean_notes else name
            prompt_list.append(entry)

            print(f"   👉 Loaded Project: '{name}' (ID: {page_id})")

        except Exception:
            continue

    print(f"✅ Total Projects Loaded: {len(prompt_list)}")
    return prompt_list, id_map


def apply_business_logic(category, data, related_project=None):
    today_str = date.today().isoformat()

    if category == "tasks":
        data["Status"] = "To Do"
        if related_project:
            data["Notes"] = f"Project: {related_project}"

    elif category == "quotes":
        data["Date"] = today_str
        raw_quote = data.get("Quote", "")
        if raw_quote:
            clean_quote = raw_quote.strip('"').strip("'").strip("“").strip("”")
            data["Quote"] = f"“{clean_quote}”"

    elif category == "movies":
        if "Status" not in data:
            data["Status"] = "Not Started"

    elif category == "podcasts":
        if data.get("Status") == "Finished":
            data["Date Listened To"] = today_str

    elif category == "youtube-videos":
        if data.get("Status") == "Watched":
            data["Date Watched"] = today_str

    elif category == "bookmarks":
        # Check URL for github.com
        if "github.com" in data.get("URL", ""):
            tags = data.get("Tags", [])
            if isinstance(tags, list) and "Github" not in tags:
                tags.append("Github")
                data["Tags"] = tags

    return data


# ==========================================
# 7. MAIN HANDLERS & EXECUTORS
# ==========================================

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

    # Special case for places (requires extra arg)
    if category == "places":
        return handle_places_logic(category, data, trips_id_map)

    # Special case for groceries (requires extra arg)
    if category in ["groceries", "fun-activities"]:
        return handle_groceries_fun_logic(category, data, inventory_map)

    # Generic lookup
    handler = LOGIC_HANDLERS.get(category, handle_default_logic)
    return handler(category, data)
