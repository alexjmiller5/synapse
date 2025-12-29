import json
from datetime import date
from google.genai import types
from config import CONFIG, PROMPTS
from clients import gemini_client

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

# --- ADDED: Missing Schema Definition ---
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
        if "response" in locals() and hasattr(response, "text"):
            print(f"   ⚠️ Failed Response Text: {response.text}")
        return [{"core_text": raw_text, "context_notes": ""}]


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
    category,
    raw_text,
    url_context=None,
    inventory_list=None,
    trips_inventory=None,
    user_context=None,
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

    # NEW: Trips Section
    trips_section = ""
    if category == "places" and trips_inventory:
        trips_section = f"--- AVAILABLE TRIPS (For 'Linked Trip' Logic) ---\n{json.dumps(trips_inventory)}"
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
        trips_section=trips_section,  # Added this
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
