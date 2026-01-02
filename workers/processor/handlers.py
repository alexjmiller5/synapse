from gcp_secrets import get_db_id
from clients import notion
from notion_utils import (
    create_page,
    update_status,
    create_cleanup_task,
    fetch_existing_page,
    build_notion_properties,
)
from external_data import get_video_channel_details


def handle_places_logic(category, data, trips_id_map):
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


def handle_groceries_fun_logic(category, data, inventory_map):
    # Map 'Name' vs 'Title' depending on DB
    search_key = "Name" if category == "groceries" else "Title"
    search_val = data.get(search_key)

    if category == "groceries" and inventory_map and search_val in inventory_map:
        page_id = inventory_map[search_val]
        print(f"   ✅ Groceries: Matched '{search_val}' (ID: {page_id}). Updating...")
        return update_status(page_id, data.get("Status")).get("url")

    # For Fun Activities, perform a smart search
    if category == "fun-activities":
        # Check for duplicates first
        existing_id = fetch_existing_page(category, search_val, key="Title")
        if existing_id:
            print(f"   ✅ Fun Activities: Matched '{search_val}'. Updating Status...")
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


def handle_youtube_logic(category, data):
    props = build_notion_properties(category, data)
    target_url = data.get("Video URL")
    video_url = target_url

    # A. DEDUPLICATION CHECK (Exact URL Match)
    if target_url:
        db_id = get_db_id("youtube-videos")
        try:
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

            if resp.get("results"):
                existing_page = resp["results"][0]
                eid = existing_page["id"]
                print(f"   ✅ Video already exists: {target_url} (ID: {eid})")
                return update_status(eid, data.get("Status", "Watched")).get("url")

        except Exception as e:
            print(f"   ⚠️ YouTube duplicate check failed: {e}")

    # B. CHANNEL RESOLUTION & LINKING
    channel_id = None

    # 1. Fetch Official Details from API
    api_channel = get_video_channel_details(video_url) if video_url else None

    if api_channel:
        official_name = api_channel["title"]
        print(f"   📺 Resolved Channel via API: {official_name}")

        # Check if exists in DB using fetch_existing_page
        channel_id = fetch_existing_page("youtube-channels", official_name, "Name")

        # If NOT found, Create it
        if not channel_id:
            print(f"   ✨ Creating new Channel: {official_name}")

            # Use the paradigm: Build a raw data dict, then pass through builder
            new_channel_data = {
                "Name": official_name,
                "Channel URL": api_channel["url"],
                "Status": "Never Subscribed",
            }

            # This handles the formatting correctly via your YAML config
            resp = create_page(
                "youtube-channels",
                build_notion_properties("youtube-channels", new_channel_data),
            )

            if resp:
                channel_id = resp["id"]
                channel_page_url = resp["url"]

                # Create Cleanup Task
                print("   🧹 Creating cleanup task to classify new channel...")
                create_cleanup_task(
                    f"Classify new Channel: {official_name}", link_url=channel_page_url
                )

    # Fallback: If API failed, try using the AI-extracted handle
    elif "channel_handle" in data:
        handle = data["channel_handle"].replace("@", "").strip()
        channel_id = fetch_existing_page("youtube-channels", handle, "Name")

    # 3. Link Channel to Video
    if channel_id:
        props["Channel"] = {"relation": [{"id": channel_id}]}

    # Remove helper fields
    if "channel_handle" in props:
        del props["channel_handle"]

    # C. CREATE VIDEO PAGE
    resp = create_page(category, props)
    created_url = resp.get("url")

    return created_url


def handle_movies_tv_logic(category, data):
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


def handle_bookmarks_logic(category, data):
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
                return (
                    f"https://www.notion.so/{resp['results'][0]['id'].replace('-','')}"
                )
        except Exception as e:
            print(f"   ⚠️ Bookmark duplicate check failed: {e}")

    # B. Apply Logic (GitHub Tags)
    if "github.com" in target_url:
        tags = data.get("Tags", [])
        if isinstance(tags, list) and "Github" not in tags:
            tags.append("Github")
            data["Tags"] = tags

    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_people_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_bucket_list_logic(category, data):
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_default_logic(category, data):
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
