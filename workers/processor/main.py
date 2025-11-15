import functions_framework
import json
import yaml
import os
import base64
import google.genai as genai
from google.genai import types
import requests
from google.cloud import secretmanager
from datetime import date

# This file stores the JSON schemas for the Gemini API calls.

# === SCHEMAS FOR AI CALL 1: CLASSIFICATION ===
CATEGORY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["Task", "Grocery", "Person", "Therapy", "Movie", "TVShow"],
        }
    },
    "required": ["category"],
}

# === SCHEMAS FOR AI CALL 2: SIMPLE EXTRACTION ===
SIMPLE_TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "original_text": {"type": "string"},
        "summarized_title": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "links": {"type": "array", "items": {"type": "string"}},
        "due_date": {"type": "string"},
    },
    "required": ["original_text", "summarized_title", "tags", "links", "due_date"],
}

SIMPLE_GROCERY_SCHEMA = {
    "type": "object",
    "properties": {
        "item_name": {"type": "string"},
        "original_text": {"type": "string"},
    },
    "required": ["item_name", "original_text"],
}

SIMPLE_TITLE_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
}

# --- Secret Manager Setup ---
# TODO: remove hardcoded configuration and have it point back to conifg.yml. This will be complicated given that the deployment only really looks at main.py. Also not sure what the best practices are here in general but I want the SSOT method
PROJECT_ID = "synapse-477401"

# Cache for secrets
SECRETS = {}
client = None


def get_secret(secret_id, version="latest"):
    """Fetches a secret from Google Secret Manager."""
    global client
    if client is None:
        try:
            client = secretmanager.SecretManagerServiceClient()
            print(f"Secret Manager client initialized for project: {PROJECT_ID}")
        except Exception as e:
            print(f"Error initializing SecretManagerServiceClient: {e}")
            return None

    if secret_id in SECRETS:
        return SECRETS[secret_id]

    try:
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version}"
        response = client.access_secret_version(request={"name": name})
        payload = response.payload.data.decode("UTF-8")
        SECRETS[secret_id] = payload
        print(f"Successfully fetched secret: {secret_id}")
        return payload
    except Exception as e:
        print(f"Error fetching secret '{secret_id}': {e}")
    return None


# --- Load Prompts ---
# TODO: Make sure the prompts.yml makes it into the deployment package
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    prompts_path = os.path.join(script_dir, "prompts.yml")
    with open(prompts_path, "r") as f:
        PROMPTS = yaml.safe_load(f)
    print("Successfully loaded prompts.yml")
except Exception as e:
    print(f"Error loading prompts.yml: {e}")
    PROMPTS = {}

# --- Global Config ---
GEMINI_API_KEY = get_secret("gemini-api-key")
gemini_client = None  # Define in global scope
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini Client initialized.")
    except Exception as e:
        print(f"FATAL: Could not initialize Gemini Client: {e}")
else:
    print("FATAL: Could not configure Gemini API. Key is missing.")

NOTION_API_KEY = get_secret("notion-api-token")
FALLBACK_NOTION_BLOCK_ID = get_secret("notion-quick-notes-last-block-id")

DATABASE_ID_SECRET_MAP = {
    "Task": "notion-tasks-db-id",
    "Grocery": "notion-groceries-db-id",
    "Person": "notion-people-db-id",
    "Therapy": "notion-therapy-db-id",
    "Movie": "notion-movies-db-id",
    "TVShow": "notion-tvshows-db-id",
}

DATABASE_IDS = {
    category: get_secret(secret_id)
    for category, secret_id in DATABASE_ID_SECRET_MAP.items()
}

# --- Helper Functions ---


def call_gemini(system_prompt, user_prompt, schema):
    """Generic helper to call the Gemini API with a specific schema."""
    if not gemini_client:
        raise Exception("Gemini client is not initialized.")

    print(f"--- Sending to Gemini (Schema: {list(schema['properties'].keys())}) ---")

    try:
        # New SDK pattern from your docs
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",  # Your model name
            contents=[
                types.Content(
                    parts=[types.Part.from_text(text=user_prompt)],
                    role="user",
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,  # Pass system prompt here
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
    except KeyError as e:
        # --- NEW DEBUGGING BLOCK ---
        print(f"--- FATAL KEYERROR in call_gemini ---")
        print(f"The google-genai SDK failed with KeyError: {e}")
        print("This almost certainly means the schema it was given is invalid.")
        print("Failing Schema:", json.dumps(schema, indent=2))
        # Re-raise the exception to be caught by the main fallback handler
        raise e
    except Exception as e:
        print(f"--- FATAL ERROR in call_gemini: {e} ---")
        raise e

    json_response = json.loads(response.text)
    print(f"--- Received from Gemini: {json_response} ---")
    return json_response


def append_to_quick_notes(raw_text):
    print(f"--- EXECUTING FALLBACK: Saving '{raw_text}' to Quick Notes ---")
    if not NOTION_API_KEY or not FALLBACK_NOTION_BLOCK_ID:
        print("FALLBACK FAILED: NOTION_API_KEY or FALLBACK_NOTION_BLOCK_ID is not set.")
        return

    url = f"https://api.notion.com/v1/blocks/{FALLBACK_NOTION_BLOCK_ID}/children"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    # This is the JSON payload you provided
    payload = {
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": raw_text}}]
                },
            }
        ]
    }

    try:
        response = requests.patch(url, headers=headers, json=payload)
        response.raise_for_status()
        print("--- FALLBACK SUCCESS: Saved to Quick Notes ---")
    except requests.exceptions.RequestException as e:
        print(f"--- FALLBACK FAILED: Error calling Notion API: {e} ---")
        if e.response:
            print(f"Response body: {e.response.text}")


def create_notion_page(category, properties):
    """
    Creates a new page in the appropriate Notion database.
    """
    print(f"--- EXECUTING SUCCESS: Creating Notion page in '{category}' ---")
    database_id = DATABASE_IDS.get(category)

    if not database_id:
        raise Exception(f"No database ID found in secrets for category: {category}")

    if not NOTION_API_KEY:
        raise Exception("NOTION_API_KEY is not set.")

    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    payload = {"parent": {"database_id": database_id}, "properties": properties}

    try:
        print(url, headers, payload)
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise an exception for bad status codes
        print(f"--- SUCCESS: Created new Notion page in '{category}' ---")
        return response.json()  # Return the new page object
    except requests.exceptions.RequestException as e:
        print(f"--- FAILED: Error calling Notion API: {e} ---")
        if e.response:
            print(f"Response body: {e.response.text}")
        # Re-raise the exception to be caught by the main try/except block
        raise e


# --- Helper Functions ---

# (Your existing call_gemini and append_to_quick_notes go here)

# --- NEW: Notion Property Builder Helpers ---
# These functions replace the need for AI Call 3


def _notion_title(text):
    """Builds a Notion 'Name' (title) property."""
    return {"title": [{"text": {"content": text}}]}


def _notion_rich_text(text):
    """Builds a Notion 'rich_text' property."""
    if not text:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": str(text)}}]}


def _notion_multi_select(tags_list):
    """Builds a Notion 'multi_select' property."""
    return {"multi_select": [{"name": tag} for tag in tags_list]}


def _notion_date(iso_date_string):
    """Builds a Notion 'date' property."""
    return {"date": {"start": iso_date_string}}


def _notion_status(name):
    """Builds a Notion 'status' property."""
    return {"status": {"name": name}}


def build_notion_properties(category, simple_data):
    """
    Takes the simple JSON from Step 2 and builds the complex
    Notion API JSON.
    """
    print(f"--- Building Notion properties for: {category} ---")

    if category == "Task":
        return {
            "Name": _notion_title(simple_data["original_text"]),
            "AI Title": _notion_rich_text(simple_data["summarized_title"]),
            "Tags": _notion_multi_select(simple_data["tags"]),
            "Links": _notion_rich_text("\n".join(simple_data["links"])),
            "Due Date": _notion_date(simple_data["due_date"]),
            "Status": _notion_status("To Do"),
        }

    elif category == "Grocery":
        return {
            "Name": _notion_title(simple_data["item_name"]),
            "Notes": _notion_rich_text(simple_data["original_text"]),
        }

    else:  # Person, Therapy, Movie, TVShow
        return {"Name": _notion_title(simple_data["title"])}


@functions_framework.cloud_event
def process_job(cloud_event):
    """
    Pub/Sub-triggered function that runs the full AI pipeline.
    This is your main worker.
    """
    print("🧠 Synapse processor worker is awake!")

    if not GEMINI_API_KEY or not PROMPTS or not NOTION_API_KEY:
        print("ERROR: Service is not configured. Missing API Keys or Prompts.")
        return  # Acknowledge the event to prevent retries

    try:
        # Get raw_text from the Pub/Sub message
        message_data = base64.b64decode(cloud_event.data["message"]["data"]).decode(
            "utf-8"
        )
        raw_text = str(message_data)  # Ensure it's a string
        if not raw_text:
            print("Error: Pub/Sub message data is empty.")
            return
        print(f"Received raw_text from Pub/Sub: {raw_text}")
    except Exception as e:
        print(f"Error decoding Pub/Sub message: {e}")
        return

    try:
        # === STEP 1: CLASSIFICATION CALL ===
        system_prompt_1 = PROMPTS["categorize_input"]
        classified_data = call_gemini(system_prompt_1, raw_text, CATEGORY_SCHEMA)
        category = classified_data.get("category", "Task")

        print(f"Step 1 Complete. Category: {category}")

        # === STEP 2: SIMPLE EXTRACTION CALL ===
        today_str = date.today().isoformat()

        if category == "Task":
            prompt_key_2 = "extract_task_details_simple"
            schema_2 = SIMPLE_TASK_SCHEMA
            prompt_2_args = {"current_date": today_str, "raw_text": raw_text}
        elif category == "Grocery":
            prompt_key_2 = "extract_grocery_details_simple"
            schema_2 = SIMPLE_GROCERY_SCHEMA
            prompt_2_args = {"raw_text": raw_text}
        else:
            prompt_key_2 = "extract_simple_title"
            schema_2 = SIMPLE_TITLE_SCHEMA
            prompt_2_args = {"category": category, "raw_text": raw_text}

        system_prompt_2 = PROMPTS[prompt_key_2].format(**prompt_2_args)
        simple_json_data = call_gemini(system_prompt_2, raw_text, schema_2)

        print(f"Step 2 Complete. Extracted: {simple_json_data}")

        # === STEP 3: BUILD NOTION PROPERTIES (Python) ===
        # This replaces the failing AI call
        final_notion_properties = build_notion_properties(category, simple_json_data)

        print(f"Step 3 Complete. Formatted for Notion: {final_notion_properties}")

        # === STEP 4: CREATE NOTION PAGE ===
        create_notion_page(category, final_notion_properties)

        print("--- JOB SUCCESS: Full pipeline complete. ---")

    except Exception as e:
        # --- FALLBACK ---
        print(f"Error during AI/Notion process: {e}. Executing fallback.")
        append_to_quick_notes(raw_text)
        print("--- JOB FAILED: Saved as Quick Note. ---")
