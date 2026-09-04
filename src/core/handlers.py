from typing import NamedTuple

from core.config import DATABASES
from core.secrets import get_db_id
from core.clients import get_notion
from core.notion_utils import (
    create_page,
    update_status,
    create_cleanup_task,
    fetch_existing_page,
    build_notion_properties,
    keys_to_ids,
    prop_id,
)
from core.external_data import (
    get_video_channel_details,
    get_youtube_video_id,
    resolve_tmdb_id,
    sanitize_youtube_url,
)
from core.life_hub import push_rows
from core.timeutils import now_utc_iso_ms


class Failed(NamedTuple):
    """A handler outcome that created nothing (a cleanup task was filed instead).

    The pipeline logs it as Error(s) with no Created Item - returning None here
    would be indistinguishable from a successful write with no URL, and the
    Executions log would claim Success over an empty result.
    """

    detail: str


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
            resp = get_notion().request(
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
                get_notion().pages.update(
                    page_id=existing_id, properties=keys_to_ids(category, update_props)
                )
                print("      ✅ Place updated.")
            except Exception as e:
                print(f"      ❌ Failed to update place: {e}")

        return f"https://www.notion.so/{existing_id.replace('-', '')}"

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
            get_notion().pages.update(
                page_id=new_page_id,
                properties={prop_id(category, "Linked Trip"): {"relation": [{"id": trip_id}]}},
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
        return update_status(page_id, data.get("Status"), category).get("url")

    # For Fun Activities, perform a smart search
    if category == "fun-activities":
        # Check for duplicates first
        existing_id = fetch_existing_page(category, search_val, key="Title")
        if existing_id:
            print(f"   ✅ Fun Activities: Matched '{search_val}'. Updating Status...")
            return update_status(existing_id, data.get("Status"), category).get("url")

        # Create new
        print(f"   ✨ Creating new {category} page.")
        resp = create_page(category, build_notion_properties(category, data))
        created_url = resp.get("url")

        # Check for Location Ambiguity (After creation, so we have a link)
        if not data.get("Location"):
            print("   ⚠️ Fun Activity Location Unknown. Creating cleanup task.")
            create_cleanup_task(f"Classify Location for: {search_val}", link_url=created_url)

        return created_url

    print(f"   ✨ Creating new {category} page.")
    return create_page(category, build_notion_properties(category, data)).get("url")


def handle_youtube_logic(category, data):
    # Strip timestamp/tracking params BEFORE dedupe + storage so the same video
    # shared with different ?t=/?si= values maps to one page.
    if data.get("Video URL"):
        data["Video URL"] = sanitize_youtube_url(data["Video URL"])

    # A YouTube URL with no video id (bare youtube.com/, a channel page) has no
    # video to store — fail loudly so the pipeline's error path logs it and creates
    # a triage task, instead of creating a junk "Could not extract Video ID" page.
    if not data.get("Video URL") or not get_youtube_video_id(data["Video URL"]):
        raise ValueError(f"No YouTube video ID in URL: {data.get('Video URL')!r}")

    props = build_notion_properties(category, data)
    target_url = data.get("Video URL")
    video_url = target_url

    # A. DEDUPLICATION CHECK (Exact URL Match)
    if target_url:
        db_id = get_db_id("youtube-videos")
        try:
            resp = get_notion().request(
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
                return update_status(eid, data.get("Status", "Watched"), category).get("url")

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
    """Movies and TV shows are life-data rows, not Notion pages.

    The TMDB id IS the row id, so an unconfident match is worse than none: we
    file a cleanup task and write nothing rather than pin a row to the wrong
    film. Everything else about the title (genres, cast, poster) is derived on
    the hub - we push only the columns we actually know, and the hub's upsert
    touches only those, so a status capture never clobbers tags or date_watched.
    """
    table = DATABASES["databases"][category]["hub_table"]
    kind = "movie" if category == "movies" else "tv"
    title = data.get("Title")

    tmdb_id = resolve_tmdb_id(kind, title)
    if not tmdb_id:
        create_cleanup_task(f"Could not resolve {title!r} on TMDB ({category})")
        return Failed(f"No confident TMDB match for {title!r} - nothing written")

    # The extractor emits "" for a field it could not fill; status is required.
    status = data.get("Status") or "Not Started"
    row = {"id": tmdb_id, "status": status, "updated_at": now_utc_iso_ms()}
    if data.get("Tags"):
        row["tags"] = data["Tags"]

    rejected = push_rows(table, [row]).get("rejected") or []
    if rejected:
        message = rejected[0].get("message")
        create_cleanup_task(f"life-data rejected {title!r}: {message}")
        return Failed(f"life-data rejected {table}/{tmdb_id}: {message}")

    print(f"   ✅ Pushed {table}/{tmdb_id}")
    return f"{table}/{tmdb_id}"


def handle_bookmarks_logic(category, data):
    target_url = data.get("URL")

    # House style: bookmark descriptions never end with a period (the prompt
    # says so too, but never trust the AI to comply).
    if isinstance(data.get("Description"), str):
        data["Description"] = data["Description"].rstrip(".")

    # A. Check for Duplicates (Exact URL Match)
    if target_url:
        db_id = get_db_id("bookmarks")
        try:
            # Specific query for URL property type
            resp = get_notion().request(
                path=f"databases/{db_id}/query",
                method="POST",
                body={"filter": {"property": "URL", "url": {"equals": target_url}}},
            )
            if resp.get("results"):
                print(f"   ✅ Bookmark already exists: {target_url}")
                return f"https://www.notion.so/{resp['results'][0]['id'].replace('-', '')}"
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
    return create_page(category, build_notion_properties(category, data)).get("url")
