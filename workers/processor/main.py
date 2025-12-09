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
# 1. CONFIGURATION ENGINE (SSOT)
# ==========================================

CONFIG = {}
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "synapse_config.yaml"), "r") as f:
        CONFIG = yaml.safe_load(f)
except Exception as e:
    print(f"❌ Critical Config Error: {e}")

# ==========================================
# 2. CLIENT INITIALIZATION
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
        except Exception:
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
        print(f"Error fetching {secret_id}: {e}")
        return None


def get_db_id(category):
    """Fetches the Secret ID for a category based on convention."""
    return get_secret(f"notion-{category}-db-id")


try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(script_dir, "prompts.yml"), "r") as f:
        PROMPTS = yaml.safe_load(f)
except Exception:
    pass

GEMINI_API_KEY = get_secret("gemini-api-key")
NOTION_API_KEY = get_secret("notion-integration-token")
FALLBACK_NOTION_BLOCK_ID = get_secret("notion-quick-notes-last-block-id")
SPOTIFY_CLIENT_ID = get_secret("spotify-client-id")
SPOTIFY_CLIENT_SECRET = get_secret("spotify-client-secret")

if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
if NOTION_API_KEY:
    notion = Client(auth=NOTION_API_KEY, notion_version="2022-06-28")
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        spotify = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
            )
        )
    except Exception:
        pass

# Pre-fetch Database IDs for those defined in Config + Logs
DATABASE_IDS = {}
for cat in list(CONFIG.get("databases", {}).keys()) + ["logs", "youtube-channels"]:
    val = get_secret(f"notion-{cat}-db-id")
    if val:
        DATABASE_IDS[cat] = val

# ==========================================
# 2. INTELLIGENT PARSING (New Step 1)
# ==========================================

PARSER_INSTRUCTION = """
You are an intelligent text parser. The user is dictating one or more items.
Your goal is to parse the input into a structured list of items.

--- DELIMITER RULES ---
1. Item Separator (@): The user uses '@' to separate distinct tasks or ideas.
    - Example: "Buy milk @ Call John" -> [Item 1: Buy milk, Item 2: Call John]

2. Context Separator ($): The user uses '$' to separate the 'Core Content' from 'Metadata/Context'.
    - Example: "Finish report $ urgent due friday" -> Core: "Finish report", Context: "urgent due friday"
    - Example: "Eli quote $ this guy dresses like he wants to get wegied" -> Core: "this guy dresses like he wants to get wegied", Context: "Eli quote"
    - EXCEPTION: Ignore '$' if it is part of a price ($50) or a variable name.
    - The context would be on either side of the '$' depending on user intent.
    - The context would be on either side of the '$'.

--- STRICT FORMATTING RULES ---
- Do NOT split text on dashes (- or —). Treat them as literal text.
- Do NOT convert dashes to newlines.
- Keep the user's capitalization and punctuation exactly as is.

--- OUTPUT FORMAT ---
Return a JSON list of objects. Each object must have:
- "core_text": The main content of the item.
- "context_notes": Any context separated by '$'. If no '$' was used, leave empty.
"""

PARSER_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "core_text": {"type": "string"},
            "context_notes": {"type": "string"},
        },
        "required": ["core_text"],
    },
}


def parse_raw_input(raw_text):
    """
    Uses Gemini to intelligently split valid delimiters while ignoring false positives (emails, prices).
    """
    print(f"🧠 Parsing raw input for delimiters...")
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[types.Content(parts=[types.Part(text=raw_text)], role="user")],
            config=types.GenerateContentConfig(
                system_instruction=PARSER_INSTRUCTION,
                response_mime_type="application/json",
                response_json_schema=PARSER_SCHEMA,
            ),
        )
        
        # LOGGING ADDED: Check raw response text
        print(f"🔍 RAW PARSER RESPONSE: {repr(response.text)}") 

        parsed = json.loads(response.text)
        print(f"   ✅ Parsed {len(parsed)} item(s).")
        return parsed
    except Exception as e:
        print(f"   ⚠️ Parsing failed: {e}. Fallback to raw text.")
        # Only log the raw text if available to avoid cluttering logs on other errors
        if 'response' in locals() and hasattr(response, 'text'):
             print(f"   ⚠️ Failed Response Text: {response.text}")
        return [{"core_text": raw_text, "context_notes": ""}]


# ==========================================
# 3. DYNAMIC HYDRATION & PROMPT GENERATION
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
    for category, details in CONFIG.get("databases", {}).items():
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

# --- PROMPT BUILDERS ---

def generate_classification_prompt(active_projects_str):
    """Builds classification prompt dynamically from descriptions."""
    category_lines = []
    for cat, details in CONFIG.get("databases", {}).items():
        if cat in ["youtube-channels", "logs"]:
            continue
        desc = details.get("description", "No description.")
        category_lines.append(f'- "{cat}": {desc}')

    return PROMPTS["categorize_template"].format(
        active_projects_list=active_projects_str,
        category_list="\n".join(category_lines),
    )


def generate_extraction_prompt(
    category, raw_text, url_context=None, inventory_list=None, user_context=None
):
    """
    Builds extraction prompt using instructions, valid options, and contexts.
    """
    db_config = CONFIG.get("databases", {}).get(category)
    if not db_config:
        return "Error: Unknown category"

    # 1. Valid Options Section
    valid_opts_lines = []
    for prop_name, rules in db_config.get("properties", {}).items():
        options = rules.get("_runtime_options") or rules.get("allowlist")
        if options:
            # CHECK THE FLAG
            is_strict = not rules.get("create_new", False)
            header = (
                f"--- VALID {prop_name.upper()} (STRICT) ---"
                if is_strict
                else f"--- EXISTING {prop_name.upper()} (CREATE NEW IF NEEDED) ---"
            )
            valid_opts_lines.append(f"{header}\n{json.dumps(options)}")

    # 2. Inventory Section
    inventory_section = ""
    if inventory_list:
        inventory_section = f"--- EXISTING INVENTORY (PREFER THESE NAMES) ---\n{json.dumps(inventory_list)}"

    # 3. Context Section
    combined_context = ""
    if url_context:
        combined_context += f"--- CONTEXT FROM URL ---\n{url_context}\n\n"
    if user_context:
        combined_context += (
            f"--- USER EXPLICIT CONTEXT (Via '$') ---\n"
            f"The user manually provided this metadata: '{user_context}'\n"
            f"Use this to determine Due Dates, Status, or specific Tags.\n"
        )

    # 4. Instructions Section
    instr_lines = []
    for prop_name, rules in db_config.get("properties", {}).items():
        instr = rules.get("instruction")
        is_virtual = rules.get("virtual")
        if instr and not is_virtual:
            formatted_instr = instr.replace("{current_date}", date.today().isoformat())
            formatted_instr = formatted_instr.replace("{raw_text}", raw_text)
            instr_lines.append(f"- `{prop_name}`: {formatted_instr}")

    return PROMPTS["extraction_template"].format(
        category=category,
        context_section=combined_context.strip(),
        valid_options_section="\n\n".join(valid_opts_lines),
        inventory_section=inventory_section,
        instructions_section="\n".join(instr_lines),
    )


def get_gemini_schema(category):
    """Generates JSON Schema from YAML + Runtime Options."""
    db_config = CONFIG.get("databases", {}).get(category)
    if not db_config:
        return {"type": "object", "properties": {"Name": {"type": "string"}}}

    schema_props = {}
    required_fields = []

    for prop_name, rules in db_config.get("properties", {}).items():
        prop_type = rules.get("type")
        if rules.get("virtual"):
            continue

        # Check if we allow creating new options
        allow_new = rules.get("create_new", False)

        field_def = {"type": "string"}

        if prop_type == "boolean":
            field_def = {"type": "boolean"}

        elif prop_type in ["multi_select", "array"]:
            opts = rules.get("_runtime_options") or rules.get("allowlist") or []
            # IF allow_new is True, we remove 'enum' so AI can write anything
            if opts and not allow_new:
                field_def = {"type": "array", "items": {"type": "string", "enum": opts}}
            else:
                field_def = {"type": "array", "items": {"type": "string"}}

        elif prop_type in ["select", "status"]:
            opts = rules.get("_runtime_options") or rules.get("allowlist") or []
            # IF allow_new is True, we remove 'enum' so AI can write anything
            # Note: Notion 'status' properties usually require specific IDs, but 'select' allows creation.
            if opts and not allow_new:
                field_def = {"type": "string", "enum": opts}
            else:
                field_def = {"type": "string"}

        schema_props[prop_name] = field_def
        if rules.get("required"):
            required_fields.append(prop_name)

    return {"type": "object", "properties": schema_props, "required": required_fields}


# ==========================================
# 4. FORMATTING HELPERS
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
# 5. PROPERTY BUILDER (YAML-DRIVEN)
# ==========================================


def build_notion_properties(category, data):
    print(f"--- Building Props for {category} ---")
    properties = {}

    db_config = CONFIG.get("databases", {}).get(category)
    if not db_config:
        return {"Name": _notion_title(data.get("Name", "Untitled"))}

    for key, value in data.items():
        if value is None:
            continue

        prop_rules = db_config.get("properties", {}).get(key)
        if not prop_rules:
            continue

        prop_type = prop_rules.get("type")

        if prop_type == "title":
            properties[key] = _notion_title(value)
        elif prop_type == "rich_text":
            properties[key] = _notion_rich_text(value)
        elif prop_type == "rich_text_list":
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
# 6. HELPERS (External APIs & Notion)
# ==========================================
def extract_url(text):
    # Regex Explanation:
    # 1. (https?://)?  -> Optional Protocol
    # 2. (www\.)?      -> Optional www
    # 3. [\w-]+\.      -> Domain name (e.g. 'google.')
    # 4. [\w.]{2,}     -> TLD (e.g. 'com', 'co.uk')
    # 5. \S* -> Any trailing path/query
    match = re.search(r"\b((?:https?://)?(?:www\.)?[\w-]+\.[\w.]{2,}\S*)", text, re.IGNORECASE)
    
    if match:
        url = match.group(1)
        # Fix: Prepend https:// if missing so requests library doesn't fail
        if not url.startswith(("http://", "https://")):
            return f"https://{url}"
        return url
    return None

def fetch_web_metadata(url):
    """Fetches Page Title (Preferring Open Graph) AND Body Text."""
    print(f"   ⏳ Fetching Web Metadata for: {url}...")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=5)
        res.raise_for_status()
        
        # 1. Scrape Body Text
        full_text = get_text(res.text)
        cleaned_body = re.sub(r'\s+', ' ', full_text).strip()[:1500]
        
        # 2. Get Title (Strategy: Open Graph -> Standard Title)
        
        # A. Try Open Graph Title first (Usually cleaner/better)
        og_match = re.search(r'<meta property="og:title" content="(.*?)"', res.text, re.IGNORECASE)
        
        # B. Fallback to standard <title> tag
        title_match = re.search(r'<title[^>]*>(.*?)</title>', res.text, re.IGNORECASE | re.DOTALL)
        
        title = "No Title Found"
        
        if og_match:
            title = og_match.group(1).strip()
            print(f"   ✅ Scraped (OG): {title}")
        elif title_match:
            title = title_match.group(1).strip()
            print(f"   ✅ Scraped (HTML): {title}")

        # --- GLOBAL CLEANUP ---
        # 1. Fix HTML entities
        title = title.replace("–", "-").replace(" ", " ").replace("&amp;", "&").replace("&#39;", "'")
        
        # 2. Remove "GitHub - " prefix (Because GitHub forces this in the title tag)
        if title.startswith("GitHub - "): 
            title = title.replace("GitHub - ", "", 1)

        return f"HTML Title: {title}\nPage Content Preview:\n{cleaned_body}..."

    except Exception as e:
        print(f"   ⚠️ Web fetch failed: {e}")
        # FALLBACK: Create Task
        print("   🧹 Triggering cleanup task for failed web scrape...")
        # create_cleanup_task(f"Manual Bookmark Entry (Scrape Failed): {url}", link_url=url)
        return "Error fetching metadata"

def get_tal_metadata(url):
    print(f"   ⏳ Fetching URL metadata from: {url}...")
    try:
        # 1. Fake User-Agent to avoid blocking
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }

        # 2. Timeout is KEY here. If it takes >5s, we abort and trigger the cleanup task.
        response = requests.get(url, headers=headers, timeout=5)
        response.raise_for_status()

        full_text = get_text(response.text)
        cleaned_text = re.sub(r"\s+", " ", full_text).strip()[:2000]

        print("   ✅ Metadata fetched successfully.")
        return f"Content:\n{cleaned_text}..."

    except Exception as e:
        print(f"   ⚠️ Metadata fetch failed: {e}")

        # 3. VERBOSE FALLBACK: Create the Task immediately
        print("   🧹 Triggering cleanup task for failed scrape...")
        create_cleanup_task(
            f"Manual Podcast Entry (Scraping Failed): {url}", link_url=url
        )

        return f"Error fetching URL: {e} (User has been notified via a Cleanup Task)"


def get_spotify_metadata(url):
    if not spotify:
        return "No Spotify Client"
    try:
        r = spotify.episode(url)
        return f"Show: {r['show']['name']}\nEp: {r['name']}\nDesc: {r['description']}"
    except Exception as e:
        return f"Spotify Error: {e}"


def get_youtube_metadata(url):
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            handle = info.get("uploader_id", "")
            return f"Title: {info.get('title')}\nHandle: {handle if handle.startswith('@') else '@'+handle}"
            
    except Exception as e:
        print(f"   ⚠️ YT Metadata fetch failed: {e}")
        # FALLBACK: Create Task
        print("   🧹 Triggering cleanup task for failed YT extraction...")
        # create_cleanup_task(f"Manual YouTube Entry (Extraction Failed): {url}", link_url=url)
        return f"YT Error: {e}"


def enrich_context(category, raw_text):
    url = extract_url(raw_text)
    if not url:
        return None

    print(f"   🔗 Enriched Context Triggered for: {url}")

    if category == "podcasts":
        if "spotify.com" in url:
            return get_spotify_metadata(url)
        if "thisamericanlife" in url:
            return get_tal_metadata(url)
    elif category == "youtube-videos":
        return get_youtube_metadata(url)
    elif category == "bookmarks":
        return fetch_web_metadata(url)

    return None


def log_job_outcome(
    raw_text, category, status, details="", created_url=None, ai_data=None
):
    print(f"--- Logging: {status} ---")
    log_id = get_db_id("logs")
    if not notion or not log_id:
        return

    props = {
        "Raw Input": _notion_title(raw_text[:2000]),
        "Code Execution": _notion_status(status),
        "Category": _notion_select(category),
        "Reported": {"checkbox": False},
        "Error Details": _notion_rich_text(str(details)[:2000]),
        "AI Summary": _notion_rich_text(
            json.dumps(ai_data, indent=2)[:2000] if ai_data else ""
        ),
    }
    if created_url:
        props["Created Item"] = {"url": created_url}
    try:
        notion.pages.create(parent={"database_id": log_id}, properties=props)
    except Exception as e:
        print(f"Log failed: {e}")


def append_to_quick_notes(raw_text):
    """Fallback: Appends text to a specific Notion Block if processing fails."""
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
        print("--- Fallback: Appended to Quick Notes ---")
    except Exception as e:
        print(f"Fallback failed: {e}")


def fetch_existing_page(category, value, key="Name"):
    db_id = get_db_id(category)
    if not notion or not db_id:
        return None

    # 1. Clean the search term for better matching
    clean_val = value.replace("The ", "").strip()

    try:
        print(f"🔍 Searching {category} for '{value}' (Smart Search: '{clean_val}')...")

        # FIX: Use raw notion.request() to bypass SDK version issues
        resp = notion.request(
            path=f"databases/{db_id}/query",
            method="POST",
            body={"filter": {"property": key, "title": {"contains": clean_val}}},
        )

        results = resp.get("results", [])

        if results:
            found_page = results[0]

            # Safely extract title
            title_prop = found_page["properties"].get(key, {}).get("title", [])
            found_title = title_prop[0]["plain_text"] if title_prop else "Unknown"
            found_id = found_page["id"]

            print(f"   ✅ Found match: '{found_title}' (ID: {found_id})")
            return found_id

        print(f"   🔸 No match found for '{clean_val}'")
    except Exception as e:
        print(f"   ❌ Search failed for '{value}': {e}")
    return None


def create_page(category, props):
    # DEBUG: See the exact structure and characters sending to Notion
    print(f"🔍 DEBUG NOTION PAYLOAD REPR: {repr(props)}")

    # Build the base arguments for the API call
    body_params = {"parent": {"database_id": get_db_id(category)}, "properties": props}

    # --- ICON LOGIC ---
    if category == "podcasts":
        body_params["icon"] = {"type": "emoji", "emoji": "🎧"}
    elif category == "movies":
        body_params["icon"] = {"type": "emoji", "emoji": "🎬"}
    elif category == "tv-shows":
        body_params["icon"] = {"type": "emoji", "emoji": "📺"}

    try:
        # Pass the parameters to the Notion SDK
        return notion.pages.create(**body_params)
    except Exception as e:
        print(f"❌ Notion Create Error: {e}")
        raise e


def update_status(page_id, status):
    print(f"🔄 Updating status for {page_id} to '{status}'...")
    try:
        return notion.pages.update(
            page_id=page_id, properties={"Status": {"status": {"name": status}}}
        )
    except Exception as e:
        print(f"   ❌ Update Status Failed: {e}")
        return None


def append_note(page_id, text):
    print(f"📎 Appending note to {page_id}...")

    def get_utf16_split(content, limit=2000):
        chunks = []
        current_chunk = []
        current_len = 0

        for char in content:
            char_len = len(char.encode("utf-16-le")) // 2

            if current_len + char_len > limit:
                chunks.append("".join(current_chunk))
                current_chunk = []
                current_len = 0

            current_chunk.append(char)
            current_len += char_len

        if current_chunk:
            chunks.append("".join(current_chunk))
        return chunks

    try:
        page = notion.pages.retrieve(page_id)
        current_notes = page["properties"].get("Notes", {}).get("rich_text", [])

        safe_notes = []

        for note_obj in current_notes:
            content = note_obj.get("text", {}).get("content", "")
            anns = note_obj.get("annotations", {})

            chunks = get_utf16_split(content)
            for chunk in chunks:
                safe_notes.append(
                    {"type": "text", "text": {"content": chunk}, "annotations": anns}
                )

        new_chunks = get_utf16_split(f"{text}")
        for chunk in new_chunks:
            safe_notes.append(
                {
                    "type": "text",
                    "text": {
                        "content": chunk if chunk != new_chunks[0] else f"\n{chunk}"
                    },
                }
            )

        notion.pages.update(
            page_id=page_id, properties={"Notes": {"rich_text": safe_notes}}
        )
        print("   ✅ Note appended successfully.")

    except Exception as e:
        print(f"   ❌ Append Note Failed: {e}")


def create_cleanup_task(desc, link_url=None):
    print(f"🧹 Creating cleanup task: {desc}")
    props = {
        "Name": _notion_title(desc),
        "AI Title": _notion_rich_text(desc),
        "Status": _notion_status("To Do"),
        "Tags": _notion_multi_select(["Chore"]),
        "Due Date": _notion_date(date.today().isoformat()),
        "Priority": _notion_select("Low"),
    }

    # If we have a link, add it to the 'Links' property
    if link_url:
        props["Links"] = _notion_rich_text(link_url)

    try:
        create_page("tasks", props)
    except Exception as e:
        print(f"   ❌ Cleanup Task Creation Failed: {e}")


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
                        {"property": "Tags", "multi_select": {"contains": "Projects"}},
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

        return prompt_list, id_map
    except Exception as e:
        print(f"❌ Project Fetch Error: {e}")
        return [], {}


# ==========================================
# 7. MAIN HANDLERS & EXECUTORS
# ==========================================
CATEGORY_SCHEMA_CLASSIFY = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": list(CONFIG.get("databases", {}).keys()),
        },
        "related_project": {"type": "string"},
    },
    "required": ["category"],
}


def execute_logic(category, data, inventory_map=None):
    print(f"⚙️ Executing Logic for: {category}")

    # --- 1. GROCERIES / FUN ACTIVITIES (Deduplication Logic) ---
    if category in ["groceries", "fun-activities"]:
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
                create_cleanup_task(f"Classify Location for: {search_val}", link_url=created_url)
            
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
                            "url": {"equals": target_url}
                        }
                    }
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
            significant_statuses = ["Priority", "Finished", "In Progress", "Watched Parts", "Gave Up"]
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
                    body={
                        "filter": {
                            "property": "URL",
                            "url": {"equals": target_url}
                        }
                    }
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


def run_pipeline(
    item_data, project_prompts, project_id_map, inventory_map, inventory_list
):
    raw_text = item_data.get("core_text", "")
    user_context = item_data.get("context_notes", "")
    full_str_for_log = (
        f"{raw_text} (Context: {user_context})" if user_context else raw_text
    )

    log_payload = {"Parser_Data": item_data, "Extractor_Data": None}

    try:
        print(
            f"🚀 Pipeline Start: {repr(raw_text)}"
        )  # Debugging the input to the pipeline

        # 2. Classify
        proj_str = ", ".join(project_prompts) if project_prompts else "None"
        cat_prompt = generate_classification_prompt(proj_str)
        classify_input = (
            f"{raw_text}\n[Context: {user_context}]" if user_context else raw_text
        )

        classified = (
            json.loads(
                gemini_client.models.generate_content(
                    model="gemini-2.5-flash-preview-09-2025",
                    contents=[
                        types.Content(
                            parts=[types.Part(text=classify_input)], role="user"
                        )
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=cat_prompt,
                        response_mime_type="application/json",
                        response_json_schema=CATEGORY_SCHEMA_CLASSIFY,
                    ),
                ).text
            )
            or {}
        )

        category = classified.get("category", "tasks")
        project = classified.get("related_project")
        print(f"🤖 Classification: {category}")

        # 3. Extract
        url_context = (
            enrich_context(category, raw_text) or "No URL"
            if category in ["podcasts", "youtube-videos", "bookmarks"]
            else None
        )
        extract_prompt = generate_extraction_prompt(
            category, raw_text, url_context, inventory_list, user_context
        )

        # DEBUG: Capture Raw AI Response before JSON Load
        ai_response_obj = gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[types.Content(parts=[types.Part(text=raw_text)], role="user")],
            config=types.GenerateContentConfig(
                system_instruction=extract_prompt,
                response_mime_type="application/json",
                response_json_schema=get_gemini_schema(category),
            ),
        )

        raw_ai_text = ai_response_obj.text
        print(f"🔍 DEBUG AI EXTRACT REPR: {repr(raw_ai_text)}")

        extracted = json.loads(raw_ai_text) or {}

        # --- KEY FIX: IGNORE AI NAME FOR TASKS ---
        # If it's a task, we force the Name to be the raw input.
        # This prevents the AI from mangling smart quotes or newlines.
        if category == "tasks":
            extracted["Name"] = raw_text

        # 4. Execute
        if not extracted:
            print("   ⚠️ Extraction returned empty.")
            extracted = {"Name": raw_text}

        extracted = apply_business_logic(category, extracted, project)
        log_payload["Extractor_Data"] = extracted

        url = None
        if project and category == "tasks":
            eid = project_id_map.get(project)
            if eid:
                print(f"   -> Appending to Project: {project}")
                note_content = extracted.get("Name", raw_text)
                # Don't append user context - that's just for categorization
                # if user_context:
                    # note_content += f" ({user_context})"
                append_note(eid, note_content)
                url = f"https://www.notion.so/{eid.replace('-','')}"
                log_payload["Extractor_Data"]["Action"] = "Appended"
            else:
                url = execute_logic(category, extracted)
        else:
            url = execute_logic(category, extracted, inventory_map)
            
            if url and url_context:
                is_scrape_error = category == "bookmarks" and "Error fetching metadata" in url_context
                is_yt_error = category == "youtube-videos" and "YT Error" in url_context
                
                if is_scrape_error or is_yt_error:
                    print(f"   🧹 Creating cleanup task for {category} failure (linked to new page)...")
                    create_cleanup_task(f"Fix Metadata for: {raw_text}", link_url=url)

        log_job_outcome(
            full_str_for_log, category, "Success", created_url=url, ai_data=log_payload
        )

    except Exception as e:
        print(f"❌ Pipeline Error: {e}")
        log_job_outcome(
            full_str_for_log, "Unknown", "Error(s)", details=e, ai_data=log_payload
        ) 
        append_to_quick_notes(full_str_for_log)


@functions_framework.cloud_event
def processor(cloud_event):
    print("🧠 Worker awake!")
    if not GEMINI_API_KEY or not PROMPTS:
        print("❌ Critical: Missing API Key or Prompts")
        return

    hydrate_dynamic_options()
    project_prompts, project_id_map = fetch_active_projects()
    inventory_map = fetch_inventory_map("groceries")
    inventory_list = list(inventory_map.keys())

    try:
        # DECODE
        full_text = str(
            base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
        )

        # DEBUG LOG: See exactly what Python sees immediately after decode
        print(f"🔍 DEBUG INPUT REPR: {repr(full_text)}")

        # STEP 1: AI PARSING
        parsed_items = parse_raw_input(full_text)
        print(f"📋 Processing Batch: {len(parsed_items)} item(s)")

        # STEP 2: LOOP
        for item in parsed_items:
            run_pipeline(
                item, project_prompts, project_id_map, inventory_map, inventory_list
            )

        print("--- BATCH COMPLETE ---")

    except Exception as e:
        print(f"❌ Critical Event Error: {e}")