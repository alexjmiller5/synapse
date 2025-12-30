import json
from datetime import date
from config import DATABASES
from gcp_secrets import get_db_id
from clients import notion
from notion_utils import (
    create_page,
    update_status,
    create_cleanup_task,
    fetch_existing_page,
    build_notion_properties,
)

# ==========================================
# 3. DYNAMIC HYDRATION
# ==========================================


def fetch_inventory_map(category):
    """
    Fetches ALL pages from a DB and returns a dict: {'Item Name': 'Page ID'}
    Uses raw .request() to bypass missing SDK methods.
    """
    db_id = get_db_id(category)
    if not notion or not db_id:
        return {}

    print(f"📚 Fetching full inventory for {category}...")
    inventory = {}
    try:
        # FIX: Use raw request to bypass "object has no attribute 'query'" error
        resp = notion.request(
            path=f"databases/{db_id}/query", method="POST", body={"page_size": 100}
        )

        for page in resp.get("results", []):
            try:
                title_prop = page["properties"].get("Name", {}).get("title", [])
                if title_prop:
                    name = title_prop[0]["plain_text"]
                    # Store as-is. AI instructions will handle mapping.
                    inventory[name] = page["id"]
            except Exception:
                continue

        print(f"   ✅ Loaded {len(inventory)} items.")
        return inventory
    except Exception as e:
        print(f"   ❌ Inventory fetch failed: {e}")
        return {}


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
    Fetches Trips.
    LOGIC MATCH: Groceries DB.
    - Prompt gets: "Trip Name (Date: ...)" (Context for AI)
    - ID Map gets: "Trip Name" -> "ID" (Simple Lookup)
    """
    db_id = get_db_id("trips")
    if not notion or not db_id:
        return [], {}

    print(f"✈️ Fetching Trips Inventory...")
    id_map = {}
    inventory_text = []

    try:
        resp = notion.request(
            path=f"databases/{db_id}/query",
            method="POST",
            body={"sorts": [{"property": "Dates", "direction": "descending"}]},
        )

        for page in resp.get("results", []):
            try:
                title_prop = page["properties"].get("Name", {}).get("title", [])
                name = title_prop[0]["plain_text"] if title_prop else "Untitled"

                date_prop = page["properties"].get("Dates", {}).get("date", {})
                date_str = date_prop.get("start", "No Date") if date_prop else "No Date"

                # --- CORRECTED LOGIC ---
                # 1. Map uses STRICT Name (Matches Groceries logic)
                id_map[name] = page["id"]

                # 2. Prompt gets Name + Date (So AI can distinguish old vs new)
                inventory_text.append(f"{name} (Date: {date_str})")

            except Exception:
                continue

        print(f"   ✅ Loaded {len(inventory_text)} trips.")
        return inventory_text, id_map
    except Exception as e:
        print(f"   ❌ Trips fetch failed: {e}")
        return [], {}


def fetch_active_projects():
    """
    Returns:
      - prompt_list: List of strings for the AI prompt ["Synapse (Context: ...)", ...]
      - id_map: Dict mapping Project Name -> Page ID {"Synapse": "123-abc"}
    """
    db_id = get_db_id("tasks")
    if not notion or not db_id:
        return [], {}

    try:
        # Use raw request to ensure it works regardless of SDK version
        response = notion.request(
            path=f"databases/{db_id}/query",
            method="POST",
            body={
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
            },
        )

        prompt_list = []
        id_map = {}

        for p in response.get("results", []):
            props = p["properties"]
            page_id = p["id"]

            # Dynamic Name Lookup
            name = "Unknown"
            for prop_name, prop_data in props.items():
                if prop_data["type"] == "title" and prop_data.get("title"):
                    name = prop_data["title"][0]["plain_text"]
                    break

            if name == "Unknown":
                continue

            # Store ID for later
            id_map[name] = page_id

            # Context Building
            notes_obj = props.get("Notes", {}).get("rich_text", [])
            notes_text = "".join([t["plain_text"] for t in notes_obj])
            clean_notes = notes_text.replace("\n", " ").strip()
            if len(clean_notes) > 150:
                clean_notes = clean_notes[:150] + "..."

            entry = f"{name} (Context: {clean_notes})" if clean_notes else name
            prompt_list.append(entry)

            print(f"   👉 Loaded Project: '{name}' (ID: {page_id})")

        print(f"✅ Total Projects Loaded: {len(prompt_list)}")
        return prompt_list, id_map
    except Exception as e:
        print(f"❌ Project Fetch Error: {e}")
        return [], {}


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


def execute_logic(category, data, inventory_map=None, trips_id_map=None):
    print(f"⚙️ Executing Logic for: {category}")

    if category == "places":
        print("   🏗️ Handling 'Places' logic...")

        # 1. Resolve Trip ID (We do this early so we can use it for Update OR Create)
        trip_name = data.get("Linked Trip")
        trip_id = None
        if trip_name and trips_id_map:
            trip_id = trips_id_map.get(trip_name)
            if not trip_id:
                print(f"      🔸 Trip Name '{trip_name}' NOT found in loaded map.")

        # Clean up data payload
        if "Linked Trip" in data:
            del data["Linked Trip"]

        # 2. Check for Duplicates (Google Maps URL)
        target_url = data.get("Google Maps URL")
        existing_id = None

        if target_url:
            db_id = get_db_id("places")
            try:
                resp = notion.request(
                    path=f"databases/{db_id}/query",
                    method="POST",
                    body={
                        "filter": {
                            "property": "Google Maps URL",
                            "url": {"equals": target_url},
                        }
                    },
                )
                if resp.get("results"):
                    existing_id = resp["results"][0]["id"]
                    print(f"      ✅ Found existing place: {existing_id}")
            except Exception as e:
                print(f"      ⚠️ Duplicate check failed: {e}")

        # 3. PATH A: UPDATE EXISTING
        if existing_id:
            print("      🔄 Updating existing Place...")

            # Construct a mini-payload of just the fields we want to update
            update_data = {}
            if "Status" in data:
                update_data["Status"] = data["Status"]
            if "Notes" in data:
                update_data["Notes"] = data["Notes"]

            # Use the helper to format them correctly for Notion
            update_props = build_notion_properties(category, update_data)

            # Manually add the Relation (since we removed it from 'data' earlier)
            if trip_id:
                update_props["Linked Trip"] = {"relation": [{"id": trip_id}]}

            if update_props:
                try:
                    notion.pages.update(page_id=existing_id, properties=update_props)
                    print("      ✅ Place updated.")
                except Exception as e:
                    print(f"      ❌ Failed to update place: {e}")

            return f"https://www.notion.so/{existing_id.replace('-','')}"

        # 4. PATH B: CREATE NEW
        print("      - Creating new Place page...")
        resp = create_page(category, build_notion_properties(category, data))
        created_url = resp.get("url")
        new_page_id = resp.get("id")
        print(f"      ✅ Page Created: {created_url}")

        # Link Trip (Post-Creation)
        if trip_id and new_page_id:
            print(f"      - Linking trip: '{trip_name}'")
            try:
                notion.pages.update(
                    page_id=new_page_id,
                    properties={"Linked Trip": {"relation": [{"id": trip_id}]}},
                )
                print("      ✅ Trip linked successfully.")
            except Exception as e:
                print(f"      ❌ Failed to link trip via API: {e}")

        return created_url

    # --- 1. GROCERIES / FUN ACTIVITIES (Deduplication Logic) ---
    if category in ["groceries", "fun-activities"]:
        # Map 'Name' vs 'Title' depending on DB
        search_key = "Name" if category == "groceries" else "Title"
        search_val = data.get(search_key)

        if category == "groceries" and inventory_map and search_val in inventory_map:
            page_id = inventory_map[search_val]
            print(
                f"   ✅ Groceries: Matched '{search_val}' (ID: {page_id}). Updating..."
            )
            return update_status(page_id, data.get("Status")).get("url")

        # For Fun Activities, perform a smart search
        if category == "fun-activities":
            # Check for duplicates first
            existing_id = fetch_existing_page(category, search_val, key="Title")
            if existing_id:
                print(
                    f"   ✅ Fun Activities: Matched '{search_val}'. Updating Status..."
                )
                return update_status(existing_id, data.get("Status")).get("url")

            # Create new
            print(f"   ✨ Creating new {category} page.")
            resp = create_page(category, build_notion_properties(category, data))
            created_url = resp.get("url")

            # Check for Location Ambiguity (After creation, so we have a link)
            if not data.get("Location"):
                print("   ⚠️ Fun Activity Location Unknown. Creating cleanup task.")
                create_cleanup_task(
                    f"Classify Location for: {search_val}", link_url=created_url
                )

            return created_url

        print(f"   ✨ Creating new {category} page.")
        return create_page(category, build_notion_properties(category, data)).get("url")

    # --- 2. YOUTUBE VIDEO LOGIC ---
    elif category == "youtube-videos":
        props = build_notion_properties(category, data)
        target_url = data.get("Video URL")

        # A. DEDUPLICATION CHECK (Exact URL Match)
        if target_url:
            db_id = get_db_id("youtube-videos")
            try:
                # Query specifically for the "Video URL" property
                resp = notion.request(
                    path=f"databases/{db_id}/query",
                    method="POST",
                    body={
                        "filter": {
                            "property": "Video URL",
                            "url": {"equals": target_url},
                        }
                    },
                )

                # If found, update status instead of creating new
                if resp.get("results"):
                    existing_page = resp["results"][0]
                    eid = existing_page["id"]
                    print(f"   ✅ Video already exists: {target_url} (ID: {eid})")

                    # Update status if the user implies a change (e.g. "Watched")
                    # Default is "Watched", so we generally want to ensure it's marked as such if re-submitted
                    return update_status(eid, data.get("Status", "Watched")).get("url")

            except Exception as e:
                print(f"   ⚠️ YouTube duplicate check failed: {e}")

        # B. CHANNEL LINKING (Only if new)
        channel_name = data.get("channel_handle", "").replace("@", "")

        # Search for Channel in Relation DB
        cid = fetch_existing_page("youtube-channels", channel_name, "Name")
        if cid:
            print(f"   -> Linked Channel ID: {cid}")
            props["Channel"] = {"relation": [{"id": cid}]}

        # Create Page
        resp = create_page(category, props)
        created_url = resp.get("url")

        # Cleanup Task if Channel Missing
        if not cid:
            print(f"   -> Channel {channel_name} not found.")
            create_cleanup_task(f"Add YT Channel: {channel_name}", link_url=created_url)

        return created_url

    # --- 3. MOVIES / TV ---
    elif category in ["movies", "tv-shows"]:
        status = data.get("Status")
        eid = fetch_existing_page(category, data["Title"], "Title")

        if eid:
            print(f"   -> Found existing {category} {eid}...")
            significant_statuses = [
                "Priority",
                "Finished",
                "In Progress",
                "Watched Parts",
                "Gave Up",
            ]
            if status in significant_statuses:
                print(f"   -> Updating status to {status}")
                return update_status(eid, status).get("url")
            return f"https://www.notion.so/{eid.replace('-','')}"

        return create_page(category, build_notion_properties(category, data)).get("url")

    # --- 4. BOOKMARKS (With URL Deduplication) ---
    elif category == "bookmarks":
        target_url = data.get("URL")

        # A. Check for Duplicates (Exact URL Match)
        if target_url:
            db_id = get_db_id("bookmarks")
            try:
                # Specific query for URL property type
                resp = notion.request(
                    path=f"databases/{db_id}/query",
                    method="POST",
                    body={"filter": {"property": "URL", "url": {"equals": target_url}}},
                )
                if resp.get("results"):
                    print(f"   ✅ Bookmark already exists: {target_url}")
                    return f"https://www.notion.so/{resp['results'][0]['id'].replace('-','')}"
            except Exception as e:
                print(f"   ⚠️ Bookmark duplicate check failed: {e}")

        # B. Apply Logic (GitHub Tags)
        if "github.com" in target_url:
            tags = data.get("Tags", [])
            if isinstance(tags, list) and "Github" not in tags:
                tags.append("Github")
                data["Tags"] = tags

        return create_page(category, build_notion_properties(category, data)).get("url")

    # --- 5. PEOPLE ---
    elif category == "people":
        return create_page(category, build_notion_properties(category, data)).get("url")

    # --- 6. BUCKET LIST ---
    elif category == "bucket-list":
        return create_page(category, build_notion_properties(category, data)).get("url")

    # --- DEFAULT (Tasks, Quotes) ---
    else:
        resp = create_page(category, build_notion_properties(category, data))
        created_url = resp.get("url")
        if category == "quotes":
            if not data.get("Context"):
                quote_preview = data.get("Quote") or data.get("Name") or "Unknown Quote"
                create_cleanup_task(
                    f"Link person to quote: {quote_preview[:30]}...",
                    link_url=created_url,
                )
        return created_url
