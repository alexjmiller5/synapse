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
        try: sm_client = secretmanager.SecretManagerServiceClient()
        except Exception: return None
    if secret_id in SECRETS: return SECRETS[secret_id]
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
    with open(os.path.join(script_dir, "prompts.yml"), "r") as f: PROMPTS = yaml.safe_load(f)
except Exception: pass

GEMINI_API_KEY = get_secret("gemini-api-key")
NOTION_API_KEY = get_secret("notion-integration-token")
FALLBACK_NOTION_BLOCK_ID = get_secret("notion-quick-notes-last-block-id")
SPOTIFY_CLIENT_ID = get_secret("spotify-client-id")
SPOTIFY_CLIENT_SECRET = get_secret("spotify-client-secret")

if GEMINI_API_KEY: gemini_client = genai.Client(api_key=GEMINI_API_KEY)
if NOTION_API_KEY: notion = Client(auth=NOTION_API_KEY, notion_version="2022-06-28")
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try: spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))
    except Exception: pass

# Pre-fetch Database IDs for those defined in Config + Logs
DATABASE_IDS = {}
for cat in list(CONFIG.get("databases", {}).keys()) + ["logs", "youtube-channels"]:
    val = get_secret(f"notion-{cat}-db-id")
    if val: DATABASE_IDS[cat] = val

# ==========================================
# 3. DYNAMIC HYDRATION & PROMPT GENERATION
# ==========================================

def fetch_inventory_map(category):
    """
    Fetches ALL pages from a DB and returns a dict: {'Item Name': 'Page ID'}
    Uses raw .request() to bypass missing SDK methods.
    """
    db_id = get_db_id(category)
    if not notion or not db_id: return {}
    
    print(f"📚 Fetching full inventory for {category}...")
    inventory = {}
    try:
        # FIX: Use raw request to bypass "object has no attribute 'query'" error
        resp = notion.request(
            path=f"databases/{db_id}/query", 
            method="POST", 
            body={"page_size": 100}
        )
        
        for page in resp.get("results", []):
            try:
                title_prop = page["properties"].get("Name", {}).get("title", [])
                if title_prop:
                    name = title_prop[0]["plain_text"]
                    # Store as-is. AI instructions will handle mapping.
                    inventory[name] = page["id"]
            except Exception: continue
            
        print(f"   ✅ Loaded {len(inventory)} items.")
        return inventory
    except Exception as e:
        print(f"   ❌ Inventory fetch failed: {e}")
        return {}

def fetch_property_options(db_id, prop_name):
    if not notion: return []
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
        if not prop: return []
        
        prop_type = prop.get("type")
        if prop_type == "select": return [o["name"] for o in prop["select"]["options"]]
        if prop_type == "multi_select": return [o["name"] for o in prop["multi_select"]["options"]]
        if prop_type == "status": return [o["name"] for o in prop["status"]["options"]]
        return []
    except Exception as e:
        print(f"   ❌ Exception fetching '{prop_name}': {e}")
        return []

def hydrate_dynamic_options():
    print("🔄 Hydrating Options...")
    for category, details in CONFIG.get("databases", {}).items():
        if category in ["logs", "youtube-channels"]: continue
        db_id = get_db_id(category)
        if not db_id: 
            print(f"   ⚠️ Skipping {category} (No DB ID)")
            continue

        for prop_name, rules in details.get("properties", {}).items():
            if rules.get("type") not in ["select", "multi_select", "status"]: continue
            
            real_options = fetch_property_options(db_id, prop_name)
            allowlist = rules.get("allowlist")
            
            # Logic: Use allowlist if present, else real options
            final_options = [opt for opt in real_options if opt in allowlist] if allowlist else real_options
            
            # Store back into CONFIG memory for Schema Generation
            rules["_runtime_options"] = final_options
            print(f"   🔹 {category} [{prop_name}]: Loaded {len(final_options)} options: {final_options}")
    print("✅ Hydration complete.")

# --- PROMPT BUILDERS ---

def generate_classification_prompt(active_projects_str):
    """Builds classification prompt dynamically from descriptions."""
    category_lines = []
    for cat, details in CONFIG.get("databases", {}).items():
        if cat in ["youtube-channels", "logs"]: continue
        desc = details.get("description", "No description.")
        category_lines.append(f'- "{cat}": {desc}')
    
    return PROMPTS["categorize_template"].format(
        active_projects_list=active_projects_str,
        category_list="\n".join(category_lines)
    )

def generate_extraction_prompt(category, raw_text, url_context=None, inventory_list=None):
    """Builds extraction prompt using instructions and valid options."""
    db_config = CONFIG.get("databases", {}).get(category)
    if not db_config: return "Error: Unknown category"

    # 1. Valid Options Section
    valid_opts_lines = []
    for prop_name, rules in db_config.get("properties", {}).items():
        options = rules.get("_runtime_options") or rules.get("allowlist")
        if options:
            valid_opts_lines.append(f"--- VALID {prop_name.upper()} ---\n{json.dumps(options)}")
    
    # 2. Inventory Section
    inventory_section = ""
    if inventory_list:
        inventory_section = f"--- EXISTING INVENTORY (PREFER THESE NAMES) ---\n{json.dumps(inventory_list)}"
    
    # 3. Instructions Section
    instr_lines = []
    for prop_name, rules in db_config.get("properties", {}).items():
        instr = rules.get("instruction")
        is_virtual = rules.get("virtual")
        if instr and not is_virtual:
            formatted_instr = instr.replace("{current_date}", date.today().isoformat())
            formatted_instr = formatted_instr.replace("{raw_text}", raw_text)
            instr_lines.append(f'- `{prop_name}`: {formatted_instr}')

    return PROMPTS["extraction_template"].format(
        category=category,
        context_section=f"--- CONTEXT FROM URL ---\n{url_context}" if url_context else "",
        valid_options_section="\n\n".join(valid_opts_lines),
        inventory_section=inventory_section, # <--- FIXED: WAS MISSING
        instructions_section="\n".join(instr_lines)
    )

def get_gemini_schema(category):
    """Generates JSON Schema from YAML + Runtime Options."""
    db_config = CONFIG.get("databases", {}).get(category)
    if not db_config: return {"type": "object", "properties": {"Name": {"type": "string"}}}

    schema_props = {}
    required_fields = []

    for prop_name, rules in db_config.get("properties", {}).items():
        prop_type = rules.get("type")
        if rules.get("virtual"): continue 

        field_def = {"type": "string"} 
        if prop_type == "boolean": field_def = {"type": "boolean"}
        elif prop_type in ["multi_select", "array"]:
            opts = rules.get("_runtime_options") or rules.get("allowlist") or []
            field_def = {"type": "array", "items": {"type": "string", "enum": opts}}
        elif prop_type in ["select", "status"]:
            opts = rules.get("_runtime_options") or rules.get("allowlist") or []
            field_def = {"type": "string", "enum": opts}
        
        schema_props[prop_name] = field_def
        if rules.get("required"): required_fields.append(prop_name)

    return {"type": "object", "properties": schema_props, "required": required_fields}

# ==========================================
# 4. FORMATTING HELPERS
# ==========================================
def _notion_title(val): return {"title": [{"text": {"content": val}}]}
def _notion_rich_text(val): return {"rich_text": [{"text": {"content": str(val)}}]} if val else {"rich_text": []}
def _notion_multi_select(val): return {"multi_select": [{"name": t} for t in val]} if val else {"multi_select": []}
def _notion_date(val): return {"date": {"start": val}}
def _notion_status(val): return {"status": {"name": val}}
def _notion_select(val): return {"select": {"name": val}} if val else None
def _notion_url(val): return {"url": val}

# ==========================================
# 5. PROPERTY BUILDER (YAML-DRIVEN)
# ==========================================

def build_notion_properties(category, data):
    print(f"--- Building Props for {category} ---")
    properties = {}
    
    db_config = CONFIG.get("databases", {}).get(category)
    if not db_config: return {"Name": _notion_title(data.get("Name", "Untitled"))}

    for key, value in data.items():
        if value is None: continue
        
        prop_rules = db_config.get("properties", {}).get(key)
        if not prop_rules: continue

        prop_type = prop_rules.get("type")

        if prop_type == "title": properties[key] = _notion_title(value)
        elif prop_type == "rich_text": properties[key] = _notion_rich_text(value)
        elif prop_type == "rich_text_list": 
            val_str = "\n".join(value) if isinstance(value, list) else str(value)
            properties[key] = _notion_rich_text(val_str)
        elif prop_type == "multi_select": properties[key] = _notion_multi_select(value)
        elif prop_type == "select": properties[key] = _notion_select(value)
        elif prop_type == "status": properties[key] = _notion_status(value)
        elif prop_type == "date": properties[key] = _notion_date(value)
        elif prop_type == "url": properties[key] = _notion_url(value)
    return properties

def apply_business_logic(category, data, related_project=None):
    today_str = date.today().isoformat()
    
    if category == "tasks":
        data["Status"] = "To Do"
        if related_project: data["Notes"] = f"Project: {related_project}"
    elif category == "quotes": 
        data["Date"] = today_str
    elif category == "movies":
        if "Status" not in data: data["Status"] = "Finished" if data.get("is_watched") else "Not Started"
    elif category == "podcasts":
        if data.get("Status") == "Finished": data["Date Listened To"] = today_str
    elif category == "youtube-videos":
        if data.get("Status") == "Watched": data["Date Watched"] = today_str
    return data

# ==========================================
# 6. HELPERS (External APIs & Notion)
# ==========================================
def extract_url(text):
    match = re.search(r"(https?://\S+)", text)
    return match.group(0) if match else None

def get_tal_metadata(url):
    try: return f"Content:\n{re.sub(r'\s+', ' ', get_text(requests.get(url).text)).strip()[:2000]}..."
    except Exception as e: return f"Error: {e}"

def get_spotify_metadata(url):
    if not spotify: return "No Spotify Client"
    try:
        r = spotify.episode(url)
        return f"Show: {r['show']['name']}\nEp: {r['name']}\nDesc: {r['description']}"
    except Exception as e: return f"Spotify Error: {e}"

def get_youtube_metadata(url):
    try:
        with yt_dlp.YoutubeDL({'quiet':True, 'skip_download':True}) as ydl:
            info = ydl.extract_info(url, download=False)
            handle = info.get('uploader_id', '')
            return f"Title: {info.get('title')}\nHandle: {handle if handle.startswith('@') else '@'+handle}"
    except Exception as e: return f"YT Error: {e}"

def enrich_context(category, raw_text):
    url = extract_url(raw_text)
    if not url: return None
    if category == "podcasts":
        if "spotify.com" in url: return get_spotify_metadata(url)
        if "thisamericanlife" in url: return get_tal_metadata(url)
    elif category == "youtube-videos": return get_youtube_metadata(url)
    return None

def log_job_outcome(raw_text, category, status, details="", created_url=None, ai_data=None):
    print(f"--- Logging: {status} ---")
    log_id = get_db_id("logs")
    if not notion or not log_id: return
    
    props = {
        "Raw Input": _notion_title(raw_text[:2000]),
        "Status": _notion_status(status),
        "Category": _notion_select(category),
        "Reported": {"checkbox": False},
        "Error Details": _notion_rich_text(str(details)[:2000]),
        "AI Summary": _notion_rich_text(json.dumps(ai_data, indent=2)[:2000] if ai_data else "")
    }
    if created_url: props["Created Item"] = {"url": created_url}
    try: notion.pages.create(parent={"database_id": log_id}, properties=props)
    except Exception as e: print(f"Log failed: {e}")

def append_to_quick_notes(raw_text):
    """Fallback: Appends text to a specific Notion Block if processing fails."""
    if not notion or not FALLBACK_NOTION_BLOCK_ID: return
    try:
        notion.blocks.children.append(
            block_id=FALLBACK_NOTION_BLOCK_ID,
            children=[{
                "object": "block", 
                "type": "paragraph", 
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": raw_text}}]}
            }]
        )
        print("--- Fallback: Appended to Quick Notes ---")
    except Exception as e:
        print(f"Fallback failed: {e}")

def fetch_existing_page(category, value, key="Name"):
    db_id = get_db_id(category)
    if not notion or not db_id: return None
    try:
        resp = notion.databases.query(database_id=db_id, filter={"property": key, "title": {"equals": value}})
        if resp.get("results"): return resp["results"][0]["id"]
    except Exception: pass
    return None

def create_page(category, props):
    print(f"📝 Sending to Notion ({category}): {json.dumps(props, default=str)}")
    try:
        return notion.pages.create(parent={"database_id": get_db_id(category)}, properties=props)
    except Exception as e:
        print(f"❌ Notion Create Error: {e}")
        raise e

def update_status(page_id, status):
    try: return notion.pages.update(page_id=page_id, properties={"Status": {"status": {"name": status}}})
    except Exception: return None

def append_note(page_id, text):
    try:
        page = notion.pages.retrieve(page_id)
        curr = page["properties"].get("Notes", {}).get("rich_text", [])
        if curr: curr.append({"type": "text", "text": {"content": "\n"}})
        curr.append({"type": "text", "text": {"content": text}})
        notion.pages.update(page_id=page_id, properties={"Notes": {"rich_text": curr}})
    except Exception: pass

def create_cleanup_task(desc):
    props = {"Name": _notion_title(desc), "Status": _notion_status("To Do"), "Tags": _notion_multi_select(["Organization"]), "Due Date": _notion_date(date.today().isoformat())}
    try: create_page("tasks", props)
    except Exception: pass

def fetch_active_projects():
    """
    Returns:
      - prompt_list: List of strings for the AI prompt ["Synapse (Context: ...)", ...]
      - id_map: Dict mapping Project Name -> Page ID {"Synapse": "123-abc"}
    """
    db_id = get_db_id("tasks")
    if not notion or not db_id: return [], {}
    
    try:
        # Use raw request to ensure it works regardless of SDK version
        response = notion.request(
            path=f"databases/{db_id}/query",
            method="POST",
            body={
                "filter": {
                    "and": [
                        {"property": "Tags", "multi_select": {"contains": "Projects"}},
                        {"property": "Status", "status": {"does_not_equal": "Completed"}}
                    ]
                },
                "page_size": 100
            }
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
            
            if name == "Unknown": continue
            
            # Store ID for later
            id_map[name] = page_id

            # Context Building
            notes_obj = props.get("Notes", {}).get("rich_text", [])
            notes_text = "".join([t["plain_text"] for t in notes_obj])
            clean_notes = notes_text.replace("\n", " ").strip()
            if len(clean_notes) > 150: clean_notes = clean_notes[:150] + "..."
            
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
        "category": {"type": "string", "enum": list(CONFIG.get("databases", {}).keys())},
        "related_project": {"type": "string"}
    },
    "required": ["category"]
}

# FIXED: Added inventory_map=None to the definition
def execute_logic(category, data, inventory_map=None):
    print(f"⚙️ Executing Logic for: {category}")
    
    if category == "groceries":
        name = data.get("Name")
        # Check if the AI-determined name exists in our map
        if inventory_map and name in inventory_map:
            page_id = inventory_map[name]
            print(f"   ✅ Matched existing item '{name}' (ID: {page_id}). Updating Status...")
            return update_status(page_id, data.get("Status")).get("url")
        else:
            print(f"   ✨ Item '{name}' not in inventory. Creating new page.")
            return create_page(category, build_notion_properties(category, data)).get("url")
    
    # Media Logic
    elif category in ["movies", "tv-shows"]:
        status = data.get("Status")
        eid = fetch_existing_page(category, data["Title"], "Title")
        if eid:
            print(f"   -> Found existing {category} {eid}, checking update logic...")
            should_up = (category=="movies" and data.get("is_watched")) or (category=="tv-shows" and status in ["Priority","Finished","In Progress"])
            if should_up: 
                print(f"   -> Updating status to {status}")
                return update_status(eid, status).get("url")
            return f"https://www.notion.so/{eid.replace('-','')}"
        return create_page(category, build_notion_properties(category, data)).get("url")
    
    # Video Logic
    elif category == "youtube-videos":
        props = build_notion_properties(category, data)
        cid = fetch_existing_page("youtube-channels", data.get("channel_handle"), "Name")
        if cid: 
            print(f"   -> Linked Channel ID: {cid}")
            props["Channel"] = {"relation": [{"id": cid}]}
        else: 
            print(f"   -> Channel {data.get('channel_handle')} not found, creating task.")
            create_cleanup_task(f"Add Channel: {data.get('channel_handle')} for '{data['Title']}'")
        return create_page(category, props).get("url")
    

    # Default
    else:
        resp = create_page(category, build_notion_properties(category, data))
        if category == "quotes": create_cleanup_task(f"Link person: {data.get('Name')[:30]}...")
        return resp.get("url")

@functions_framework.cloud_event
def processor(cloud_event):
    print("🧠 Worker awake!")
    if not GEMINI_API_KEY or not PROMPTS: 
        print("❌ Critical: Missing API Key or Prompts")
        return
    
    hydrate_dynamic_options()

    raw_text, category, extracted, url = "", "Unknown", {}, None
    try:
        raw_text = str(base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8"))
        print(f"🚀 Processing: '{raw_text}'")
        
        # 1. Active Projects (FETCH ONCE, USE MAP LATER)
        project_prompts, project_id_map = fetch_active_projects()
        proj_str = ", ".join(project_prompts) if project_prompts else "None"
        print(f"📋 Active Projects: {proj_str}")
        
        # 2. Classify
        cat_prompt = generate_classification_prompt(proj_str)
        classified = json.loads(gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[types.Content(parts=[types.Part(text=raw_text)], role="user")],
            config=types.GenerateContentConfig(system_instruction=cat_prompt, 
            response_mime_type="application/json", response_json_schema=CATEGORY_SCHEMA_CLASSIFY)).text)
        
        category = classified.get("category", "tasks")
        project = classified.get("related_project")
        print(f"🤖 Classification Result: {json.dumps(classified)}")
        
        # 3. PRE-FETCH INVENTORY (If Groceries)
        inventory_map = {}
        inventory_list = []
        if category == "groceries":
            inventory_map = fetch_inventory_map("groceries")
            inventory_list = list(inventory_map.keys())

        # 4. Extract
        url_context = enrich_context(category, raw_text) or "No URL" if category in ["podcasts", "youtube-videos"] else None
        if url_context: print(f"🔗 Enriched Context: {url_context[:100]}...")

        extract_prompt = generate_extraction_prompt(category, raw_text, url_context, inventory_list)
        
        extracted = json.loads(gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[types.Content(parts=[types.Part(text=raw_text)], role="user")],
            config=types.GenerateContentConfig(system_instruction=extract_prompt, 
            response_mime_type="application/json", response_json_schema=get_gemini_schema(category))).text)
        
        print(f"🤖 Raw Extraction: {json.dumps(extracted)}")
        
        # 5. Exec
        extracted = apply_business_logic(category, extracted, project)
        print(f"✨ Final Payload (Post-Logic): {json.dumps(extracted)}")
        
        # LOGIC CHANGE: Use the ID map we already fetched!
        if project and category == "tasks":
            # Look up ID in the map we created in step 1
            eid = project_id_map.get(project)
            
            if eid:
                print(f"   -> Appending to Project: {project} (ID: {eid})")
                append_note(eid, extracted.get("Name", raw_text))
                url = f"https://www.notion.so/{eid.replace('-','')}"
            else:
                print(f"   ⚠️ Project '{project}' not found in active map. Creating new task.")
                url = execute_logic(category, extracted)
        else:
            url = execute_logic(category, extracted, inventory_map)

        log_job_outcome(raw_text, category, "Success", created_url=url, ai_data=extracted)
        print("--- JOB SUCCESS ---")

    except Exception as e:
        print(f"❌ Error Traceback: {e}")
        log_job_outcome(raw_text, category, "Failure", details=e, ai_data=extracted)
        append_to_quick_notes(raw_text)

    except Exception as e:
        print(f"❌ Error Traceback: {e}")
        log_job_outcome(raw_text, category, "Failure", details=e, ai_data=extracted)
        append_to_quick_notes(raw_text)