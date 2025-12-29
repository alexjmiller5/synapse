from workers.processor.notion_utils import create_page, update_status, find_page_by_title, append_blocks
from notion_client import Client
from gcp_secrets import get_db_id
from clients import notion


def handle_places(data, trips_map=None):
    """
    Logic: Create Place -> Check for 'Linked Trip' -> Link if found.
    """
    # 1. Extract Trip Name if present
    trip_name = data.pop("Linked Trip", None)

    # 2. Create the Place Page
    resp = create_page("places", data)
    if not resp:
        return None

    page_id = resp["id"]
    page_url = resp["url"]

    # 3. Link to Trip (if map provided)
    if trip_name and trips_map:
        trip_id = trips_map.get(trip_name)
        if trip_id:
            try:
                notion.pages.update(
                    page_id=page_id,
                    properties={"Linked Trip": {"relation": [{"id": trip_id}]}},
                )
            except Exception as e:
                print(f"⚠️ Failed to link trip: {e}")

    return page_url


def handle_groceries(data, inventory_map):
    """
    Logic: Check inventory first. If exists -> Update Status. Else -> Create New.
    """
    name = data.get("Name")
    status = data.get("Status", "To Buy")

    # 1. Check Inventory
    if name in inventory_map:
        pid = inventory_map[name]
        update_status(pid, status)
        return f"https://notion.so/{pid.replace('-','')}"

    # 2. Create New
    return create_page("groceries", data)["url"]


def handle_media(category, data):
    """
    Logic: Check if movie/tv show exists. If so -> Update Status.
    """
    title = data.get("Title") or data.get("Name")
    status = data.get("Status")

    existing_id = find_page_by_title(category, title, property_name="Title")

    if existing_id:
        if status:
            update_status(existing_id, status)
        return f"https://notion.so/{existing_id.replace('-','')}"

    return create_page(category, data)["url"]


def handle_youtube(data):
    """
    Logic: Check duplicate URL. Link Channel if possible.
    """
    video_url = data.get("Video URL")

    # 1. Duplicate Check
    if video_url:
        # (Simplified manual query here for specific duplicate check)
        db_id = get_db_id("youtube-videos")
        try:
            resp = notion.databases.query(
                database_id=db_id,
                filter={"property": "Video URL", "url": {"equals": video_url}},
            )
            if resp["results"]:
                # Already exists, just mark watched if implied
                existing_id = resp["results"][0]["id"]
                update_status(existing_id, data.get("Status", "Watched"))
                return f"https://notion.so/{existing_id.replace('-','')}"
        except:
            pass

    # 2. Link Channel (Logic simplified)
    # You would look up the channel ID here similar to other lookups

    return create_page("youtube-videos", data)["url"]


def handle_standard_category(category, data):
    """Default handler for generic categories."""
    resp = create_page(category, data)
    return resp["url"] if resp else None
