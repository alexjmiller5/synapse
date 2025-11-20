import functions_framework
import json
import yaml
import os
import re
import base64
import requests
from datetime import date
import google.genai as genai
from google.genai import types
from google.cloud import secretmanager
from notion_client import Client
from inscriptis import get_text
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp

# ==========================================
# 1. PROPERTY TYPE DEFINITIONS
# ==========================================
NOTION_PROPERTY_TYPES = {
    "Name": "title",
    "Title": "title",
    "Description": "title",
    "Episode Title": "title",
    "AI Title": "rich_text",
    "Notes": "rich_text",
    "Context": "rich_text",
    "Tags": "multi_select",
    "Genres": "multi_select",
    "Famous Cast Members": "multi_select",
    "Links": "rich_text_list",
    "Due Date": "date",
    "Date": "date",
    "Date Watched": "date",
    "Date Listened To": "date",
    "Status": "status",
    "Podcast Name": "select",
    "Producer": "select",
    "Director": "select",
    "URL": "url",
    "Video URL": "url",
}

# ==========================================
# 2. SCHEMAS
# ==========================================
# (Your Schemas remain exactly the same...)
SIMPLE_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "Name": {"type": "string"},
        "AI Title": {"type": "string"},
        "Tags": {"type": "array", "items": {"type": "string"}},
        "Links": {"type": "array", "items": {"type": "string"}},
        "Due Date": {"type": "string"},
    },
    "required": ["Name", "AI Title", "Tags", "Links", "Due Date"],
}

SIMPLE_GROCERY_SCHEMA = {
    "type": "object",
    "properties": {
        "Name": {"type": "string"},
        "Notes": {"type": "string"},
    },
    "required": ["Name", "Notes"],
}

SIMPLE_TITLE_SCHEMA = {
    "type": "object",
    "properties": {"Name": {"type": "string"}},
    "required": ["Name"],
}

SIMPLE_QUOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "Name": {"type": "string"},
        "Context": {"type": "string"},
    },
    "required": ["Name", "Context"],
}

SIMPLE_IDEA_SCHEMA = {
    "type": "object",
    "properties": {
        "Description": {"type": "string"},
        "Tags": {"type": "array", "items": {"type": "string"}},
        "Status": {
            "type": "string",
            "enum": ["Not started", "In progress", "Bad Idea", "Already Exists"],
        },
    },
    "required": ["Description", "Tags", "Status"],
}

SIMPLE_MOVIE_SCHEMA = {
    "type": "object",
    "properties": {
        "Title": {"type": "string"},
        "is_watched": {"type": "boolean"},
        "Genres": {"type": "array", "items": {"type": "string"}},
        "Director": {"type": "string"},
        "Producer": {"type": "string"},
        "Famous Cast Members": {"type": "array", "items": {"type": "string"}},
        "original_text": {"type": "string"},
    },
    "required": ["Title", "is_watched", "Genres", "original_text"],
}

SIMPLE_TVSHOW_SCHEMA = {
    "type": "object",
    "properties": {
        "Title": {"type": "string"},
        "Status": {
            "type": "string",
            "enum": ["Priority", "Finished", "In Progress", "Not Started"],
        },
        "Genres": {"type": "array", "items": {"type": "string"}},
        "Producer": {"type": "string"},
        "Famous Cast Members": {"type": "array", "items": {"type": "string"}},
        "original_text": {"type": "string"},
    },
    "required": ["Title", "Status", "Genres", "original_text"],
}

SIMPLE_PODCAST_SCHEMA = {
    "type": "object",
    "properties": {
        "Podcast Name": {"type": "string"},
        "Episode Title": {"type": "string"},
        "Producer": {"type": "string"},
        "Genres": {"type": "array", "items": {"type": "string"}},
        "Status": {
            "type": "string",
            "enum": ["Not Started", "In Progress", "Finished"],
        },
        "URL": {"type": "string"},
    },
    "required": ["Podcast Name", "Episode Title", "Status", "URL"],
}

SIMPLE_VIDEO_SCHEMA = {
    "type": "object",
    "properties": {
        "Title": {"type": "string"},
        "channel_handle": {"type": "string"},
        "Status": {
            "type": "string",
            "enum": ["Priority", "Not Started", "In Progress", "Watched"],
        },
        "Video URL": {"type": "string"},
    },
    "required": ["Title", "channel_handle", "Status", "Video URL"],
}

CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [
                "Task",
                "Grocery",
                "Shopping",
                "Person",
                "Idea",
                "Quote",
                "Activity",
                "BucketList",
                "Language",
                "Movie",
                "TVShow",
                "TVEpisode",
                "Podcast",
                "Book",
                "Game",
                "Video",
                "Channel",
            ],
        },
        "related_project": {
            "type": "string",
            "description": "The exact name of the active project this input relates to, if applicable.",
        },
    },
    "required": ["category"],
}

CATEGORY_CONFIG = {
    "Task": {"prompt": "extract_task_details_simple", "schema": SIMPLE_TASK_SCHEMA},
    "Grocery": {
        "prompt": "extract_grocery_details_simple",
        "schema": SIMPLE_GROCERY_SCHEMA,
    },
    "Shopping": {
        "prompt": "extract_grocery_details_simple",
        "schema": SIMPLE_GROCERY_SCHEMA,
    },
    "Quote": {"prompt": "extract_quote_details", "schema": SIMPLE_QUOTE_SCHEMA},
    "Idea": {"prompt": "extract_idea_details", "schema": SIMPLE_IDEA_SCHEMA},
    "Movie": {"prompt": "extract_movie_details", "schema": SIMPLE_MOVIE_SCHEMA},
    "TVShow": {"prompt": "extract_tvshow_details", "schema": SIMPLE_TVSHOW_SCHEMA},
    "Podcast": {"prompt": "extract_podcast_details", "schema": SIMPLE_PODCAST_SCHEMA},
    "Video": {"prompt": "extract_video_details", "schema": SIMPLE_VIDEO_SCHEMA},
    "DEFAULT": {"prompt": "extract_simple_title", "schema": SIMPLE_TITLE_SCHEMA},
}

DATABASE_ID_SECRET_MAP = {
    "Task": "notion-tasks-db-id",
    "Grocery": "notion-groceries-db-id",
    "Shopping": "notion-shopping-db-id",
    "Person": "notion-people-db-id",
    "Idea": "notion-ideas-db-id",
    "Quote": "notion-quotes-db-id",
    "Activity": "notion-fun-activities-db-id",
    "BucketList": "notion-bucket-list-db-id",
    "Language": "notion-languages-db-id",
    "Movie": "notion-movies-db-id",
    "TVShow": "notion-tv-shows-db-id",
    "TVEpisode": "notion-tv-episodes-db-id",
    "Podcast": "notion-podcasts-db-id",
    "Book": "notion-books-db-id",
    "Game": "notion-video-games-db-id",
    "Video": "notion-youtube-videos-db-id",
    "Channel": "notion-youtube-channels-db-id",
    "Logs": "notion-logs-db-id",
}

# ==========================================
# 3. CLIENT INITIALIZATION
# ==========================================
PROJECT_ID = "synapse-477401"
SECRETS = {}
sm_client = None
gemini_client = None
notion = None
spotify = None
PROMPTS = {}


def get_secret(secret_id, version="latest"):
    global sm_client
    if sm_client is None:
        try:
            sm_client = secretmanager.SecretManagerServiceClient()
        except Exception as e:
            print(f"Error initializing SecretManager: {e}")
            return None

    if secret_id in SECRETS:
        return SECRETS[secret_id]

    try:
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version}"
        response = sm_client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("UTF-8")
        SECRETS[secret_id] = payload
        return payload
    except Exception as e:
        print(f"Error fetching secret '{secret_id}': {e}")
        return None


try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "prompts.yml"), "r") as f:
        PROMPTS = yaml.safe_load(f)
except Exception as e:
    print(f"Error loading prompts.yml: {e}")

GEMINI_API_KEY = get_secret("gemini-api-key")
NOTION_API_KEY = get_secret("notion-integration-token")
FALLBACK_NOTION_BLOCK_ID = get_secret("notion-quick-notes-last-block-id")
SPOTIFY_CLIENT_ID = get_secret("spotify-client-id")
SPOTIFY_CLIENT_SECRET = get_secret("spotify-client-secret")

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
if NOTION_API_KEY:
    notion = Client(auth=NOTION_API_KEY)
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
            )
        )
    except Exception:
        pass

DATABASE_IDS = {cat: get_secret(sid) for cat, sid in DATABASE_ID_SECRET_MAP.items()}


# ==========================================
# 4. FORMATTING HELPERS (MOVED UP)
# ==========================================
def _notion_title(val):
    return {"title": [{"text": {"content": val}}]}


def _notion_rich_text(val):
    return (
        {"rich_text": [{"text": {"content": str(val)}}]} if val else {"rich_text": []}
    )


def _notion_multi_select(val):
    return {"multi_select": [{"name": t} for t in val]} if val else {"multi_select": []}


def _notion_date(val):
    return {"date": {"start": val}}


def _notion_status(val):
    return {"status": {"name": val}}


def _notion_select(val):
    return {"select": {"name": val}} if val else None


def _notion_url(val):
    return {"url": val}


# ==========================================
# 5. ENRICHMENT HELPERS
# ==========================================
def log_job_outcome(
    raw_text, category, status, details="", created_url=None, ai_data=None
):
    """
    Creates a log entry in the Synapse Logs DB.
    """
    print(f"--- Logging outcome: {status} ---")
    log_db_id = DATABASE_IDS.get("Logs")
    if not notion or not log_db_id:
        return

    ai_summary = json.dumps(ai_data, indent=2) if ai_data else "No data"

    props = {
        "Raw Input": _notion_title(raw_text[:2000]),
        "Status": _notion_status(status),
        "Category": _notion_select(category),
        "Reported": {"checkbox": False},
        "Error Details": _notion_rich_text(str(details)[:2000]),
        "AI Summary": _notion_rich_text(ai_summary[:2000]),
    }

    if created_url:
        props["Created Item"] = {"url": created_url}

    try:
        notion.pages.create(parent={"database_id": log_db_id}, properties=props)
        print("--- Log Entry Created ---")
    except Exception as e:
        print(f"Failed to write log: {e}")


def extract_url(text):
    match = re.search(r"(https?://\S+)", text)
    return match.group(0) if match else None


def get_tal_metadata(url):
    try:
        html = requests.get(url).text
        text = get_text(html)
        clean_text = re.sub(r"\s+", " ", text).strip()
        return f"Content from URL:\n{clean_text[:2000]}..."
    except Exception as e:
        return f"Error scraping TAL: {e}"


def get_spotify_metadata(url):
    if not spotify:
        return "Spotify client not configured."
    try:
        results = spotify.episode(url)
        return (
            f"Spotify Metadata:\nShow: {results['show']['name']}\n"
            f"Episode: {results['name']}\nPublisher: {results['show']['publisher']}\n"
            f"Description: {results['description']}"
        )
    except Exception as e:
        return f"Error fetching Spotify data: {e}"


def get_youtube_metadata(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            handle = info.get("uploader_id", "")
            handle = handle if handle.startswith("@") else f"@{handle}"
            return (
                f"YouTube Metadata:\nTitle: {info.get('title', 'Unknown')}\n"
                f"Channel Name: {info.get('uploader', 'Unknown')}\nChannel Handle: {handle}"
            )
    except Exception as e:
        return f"Error fetching YouTube data: {e}"


def enrich_context(category, raw_text):
    url = extract_url(raw_text)
    if not url:
        return None
    if category == "Podcast":
        if "spotify.com" in url:
            return get_spotify_metadata(url)
        if "thisamericanlife.org" in url:
            return get_tal_metadata(url)
    elif category == "Video":
        return get_youtube_metadata(url)
    return None


# ==========================================
# 6. NOTION INTERACTION
# ==========================================
def fetch_existing_page_by_title(category, title_text, title_key="Name"):
    if not notion or not DATABASE_IDS.get(category):
        return None
    try:
        response = notion.databases.query(
            database_id=DATABASE_IDS[category],
            filter={"property": title_key, "title": {"equals": title_text}},
        )
        if response.get("results"):
            return response["results"][0]["id"]
    except Exception:
        pass
    return None


def fetch_active_projects():
    if not notion or not DATABASE_IDS.get("Task"):
        return []
    try:
        response = notion.databases.query(
            database_id=DATABASE_IDS["Task"],
            filter={
                "and": [
                    {"property": "Tags", "multi_select": {"contains": "Project"}},
                    {"property": "Status", "status": {"does_not_equal": "Done"}},
                ]
            },
            page_size=100,
        )
        return [
            p["properties"]["Name"]["title"][0]["text"]["content"]
            for p in response.get("results", [])
            if p["properties"].get("Name", {}).get("title")
        ]
    except Exception:
        return []


def create_notion_page(category, properties):
    if not notion or not DATABASE_IDS.get(category):
        raise Exception(f"Notion client or DB missing for {category}")
    print(f"--- Creating Page in '{category}' ---")
    return notion.pages.create(
        parent={"database_id": DATABASE_IDS[category]}, properties=properties
    )


def update_page_status(page_id, status_name, status_key="Status"):
    if not notion:
        return None
    print(f"--- Updating Page {page_id} Status to '{status_name}' ---")
    try:
        return notion.pages.update(
            page_id=page_id, properties={status_key: {"status": {"name": status_name}}}
        )
    except Exception as e:
        print(f"Failed to update status: {e}")
        return None


def append_to_quick_notes(raw_text):
    if not notion or not FALLBACK_NOTION_BLOCK_ID:
        return
    try:
        notion.blocks.children.append(
            block_id=FALLBACK_NOTION_BLOCK_ID,
            children=[
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": raw_text}}]
                    },
                }
            ],
        )
    except Exception:
        pass


def create_manual_cleanup_task(description):
    print(f"--- Creating Cleanup Task: {description} ---")
    props = {
        "Name": _notion_title(description),
        "Status": _notion_status("To Do"),
        "Tags": _notion_multi_select(["Organization"]),
        "Due Date": _notion_date(date.today().isoformat()),
    }
    try:
        create_notion_page("Task", props)
    except Exception:
        pass


def append_text_to_property(page_id, property_name, new_text):
    """Appends text to an existing rich_text property on a Notion page."""
    if not notion:
        return
    print(f"--- Appending text to Page {page_id}, Prop '{property_name}' ---")
    try:
        # 1. Retrieve current content
        page = notion.pages.retrieve(page_id)
        current_rich_text = (
            page["properties"].get(property_name, {}).get("rich_text", [])
        )

        # 2. Append new text (prepend newline if existing text exists)
        if current_rich_text:
            current_rich_text.append({"type": "text", "text": {"content": "\n"}})
        current_rich_text.append({"type": "text", "text": {"content": new_text}})

        # 3. Update
        notion.pages.update(
            page_id=page_id,
            properties={property_name: {"rich_text": current_rich_text}},
        )
        print("--- Append Success ---")
    except Exception as e:
        print(f"Failed to append text: {e}")


# ==========================================
# 7. PROPERTY BUILDER (UNIVERSAL)
# ==========================================


def apply_business_logic(category, data, related_project=None):
    """
    Enforces side-effects and logic branching BEFORE mapping.
    Injects or modifies keys in 'data' to match NOTION_PROPERTY_TYPES.
    """
    today_str = date.today().isoformat()

    # 1. Task Logic
    if category == "Task":
        data["Status"] = "To Do"
        if related_project:
            data["Notes"] = f"Project: {related_project}"

    # 2. Quote Logic
    elif category == "Quote":
        data["Date"] = today_str

    # 3. Movie Logic (Calculate Status from bool)
    elif category == "Movie":
        if "Status" not in data:
            data["Status"] = "Finished" if data.get("is_watched") else "Not Started"

    # 4. Podcast Logic (If Finished -> Date Listened)
    elif category == "Podcast":
        if data.get("Status") == "Finished":
            data["Date Listened To"] = today_str

    # 5. Video Logic (If Watched -> Date Watched)
    elif category == "Video":
        if data.get("Status") == "Watched":
            data["Date Watched"] = today_str

    return data


def build_notion_properties(category, data):
    """
    Universal builder. Loops through data keys, finds type in map, formats it.
    """
    print(f"--- Building Universal Props for {category} ---")
    properties = {}

    for key, value in data.items():
        # Check if this key is a known Notion Property
        if key in NOTION_PROPERTY_TYPES:
            prop_type = NOTION_PROPERTY_TYPES[key]

            # Skip empty values
            if value is None:
                continue

            # Dispatch to helper
            if prop_type == "title":
                properties[key] = _notion_title(value)
            elif prop_type == "rich_text":
                properties[key] = _notion_rich_text(value)
            elif prop_type == "rich_text_list":
                # Join list strings for Rich Text fields
                val_str = "\n".join(value) if isinstance(value, list) else str(value)
                properties[key] = _notion_rich_text(val_str)
            elif prop_type == "multi_select":
                properties[key] = _notion_multi_select(value)
            elif prop_type == "select":
                properties[key] = _notion_select(value)
            elif prop_type == "status":
                properties[key] = _notion_status(value)
            elif prop_type == "date":
                properties[key] = _notion_date(value)
            elif prop_type == "url":
                properties[key] = _notion_url(value)

    return properties


# ==========================================
# 8. HANDLERS & MAIN
# ==========================================


def handle_media_logic(category, data):
    """Returns the URL of the created or updated page."""
    title_key = "movie_title" if category == "Movie" else "tvshow_title"
    status_val = data.get("Status")

    existing_id = fetch_existing_page_by_title(
        category, data["Title"], title_key="Title"
    )

    if existing_id:
        # Logic: Update if status implies progress/completion
        should_update = False
        if category == "Movie" and data.get("is_watched"):
            should_update = True
        if category == "TVShow" and status_val in [
            "Priority",
            "Finished",
            "In Progress",
        ]:
            should_update = True

        if should_update:
            resp = update_page_status(existing_id, status_val)
            print(f"{category} '{data['Title']}' updated to {status_val}.")
            return resp.get("url") if resp else None
        else:
            print(f"{category} '{data['Title']}' exists. No update.")
            # Return the existing URL even if we didn't update it, so the log links to it
            return f"https://www.notion.so/{existing_id.replace('-', '')}"
    else:
        props = build_notion_properties(category, data)
        resp = create_notion_page(category, props)
        return resp.get("url")


def handle_video_logic(category, data):
    """Returns the URL of the created video page."""
    props = build_notion_properties(category, data)

    channel_handle = data.get("channel_handle")
    channel_id = fetch_existing_page_by_title("Channel", channel_handle)
    if channel_id:
        props["Channel"] = {"relation": [{"id": channel_id}]}

    resp = create_notion_page(category, props)

    if not channel_id:
        create_manual_cleanup_task(
            f"Add YouTube Channel: {channel_handle} for video '{data['Title']}'"
        )

    return resp.get("url")


def execute_category_action(category, data):
    """Routes logic and returns the URL of the primary item created/touched."""
    if category in ["Movie", "TVShow"]:
        return handle_media_logic(category, data)
    elif category == "Video":
        return handle_video_logic(category, data)
    else:
        props = build_notion_properties(category, data)
        resp = create_notion_page(category, props)

        if category == "Quote":
            create_manual_cleanup_task(
                f"Link person to quote: '{data.get('Name','...')[:30]}...'"
            )

        return resp.get("url")


def call_gemini(system_prompt, user_prompt, schema):
    if not gemini_client:
        raise Exception("Gemini not initialized")
    print(f"--- Gemini Call: {list(schema['properties'].keys())} ---")
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[
                types.Content(
                    parts=[types.Part.from_text(text=user_prompt)], role="user"
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Gemini Error: {e}")
        raise e


@functions_framework.cloud_event
def processor(cloud_event):
    print("🧠 Synapse processor worker is awake!")
    if not GEMINI_API_KEY or not PROMPTS or not NOTION_API_KEY:
        print("ERROR: Configuration missing.")
        return

    # Initialize variables for logging
    raw_text = ""
    category = "Unknown"
    extracted_data = {}
    created_url = None

    try:
        message_data = base64.b64decode(cloud_event.data["message"]["data"]).decode(
            "utf-8"
        )
        raw_text = str(message_data)
        if not raw_text:
            return
        print(f"Processing: {raw_text}")
    except Exception as e:
        print(f"Pub/Sub Error: {e}")
        return

    try:
        # Step 0: Context
        active_projects = fetch_active_projects()
        projects_str = ", ".join(active_projects) if active_projects else "None"

        # Step 1: Classification
        sys_prompt_1 = PROMPTS["categorize_input"].format(
            active_projects_list=projects_str
        )
        classified = call_gemini(sys_prompt_1, raw_text, CATEGORY_SCHEMA)
        category = classified.get("category", "Task")
        related_project = classified.get("related_project")
        print(f"Classified as: {category}")

        # Step 2: Preparation
        config = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["DEFAULT"])

        prompt_args = {"raw_text": raw_text}
        if category == "Task":
            prompt_args["current_date"] = date.today().isoformat()
        elif category in ["Podcast", "Video"]:
            print(f"--- Enriching {category} Data ---")
            prompt_args["url_context"] = (
                enrich_context(category, raw_text) or "No URL found."
            )
        elif category not in CATEGORY_CONFIG:
            prompt_args["category"] = category

        # Step 3: AI Extraction
        sys_prompt_2 = PROMPTS[config["prompt"]].format(**prompt_args)
        extracted_data = call_gemini(sys_prompt_2, raw_text, config["schema"])
        print(f"Extracted: {extracted_data}")

        # Step 4: Business Logic
        processed_data = apply_business_logic(category, extracted_data, related_project)

        # Step 5: Execution & URL Capture
        if related_project and category == "Task":
            print(f"--- Detected Related Project: {related_project} ---")
            project_page_id = fetch_existing_page_by_title("Task", related_project)

            if project_page_id:
                # Append to Project
                text_to_append = extracted_data.get("Name") or raw_text
                append_text_to_property(project_page_id, "Notes", text_to_append)
                created_url = f"https://www.notion.so/{project_page_id.replace('-', '')}"  # Construct Project URL
                print("--- Project Note Appended ---")
            else:
                # Fallback to new task
                print(
                    f"Project '{related_project}' not found. Creating linked task instead."
                )
                created_url = execute_category_action(category, processed_data)
        else:
            created_url = execute_category_action(category, processed_data)

        # LOG SUCCESS
        log_job_outcome(
            raw_text,
            category,
            "Success",
            created_url=created_url,
            ai_data=extracted_data,
        )
        print("--- JOB SUCCESS ---")

    except Exception as e:
        print(f"Pipeline Error: {e}")
        # LOG FAILURE
        log_job_outcome(
            raw_text, category, "Failure", details=str(e), ai_data=extracted_data
        )
        append_to_quick_notes(raw_text)
