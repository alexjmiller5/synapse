from datetime import date
from core.config import DATABASES
from core.secrets import get_db_id
from core.clients import notion
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
    if not notion or not db_id:
        return []

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
        db = notion.databases.retrieve(db_id)

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
        if details.get("helper"):
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
