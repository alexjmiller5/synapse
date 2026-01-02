import json
from datetime import date
from google.genai import types
from google.genai.errors import ServerError
from config import DATABASES, PROMPTS
from clients import gemini_client
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from schemas import PARSER_SCHEMA


def safe_json_load(text):
    """Helper to parse JSON and raise specific error if it fails."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("Malformed JSON from AI")


@retry(
    # Retry on 500s (Server Errors) OR ValueError (Bad JSON)
    retry=retry_if_exception_type((ServerError, ValueError)),
    # Wait 2s, 4s, 8s... up to 60s
    wait=wait_exponential(multiplier=1, min=2, max=60),
    # Stop after 4 attempts (approx 30s total wait)
    stop=stop_after_attempt(4),
)
def generate_with_retry(model, contents, config):
    """
    Robust wrapper for Gemini API calls.
    Catches 503s and Bad JSON, retrying automatically.
    """
    response = gemini_client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    # We validate JSON immediately to force a retry if it's bad
    # This works because we are using structured outputs (response_mime_type="application/json")
    if config.response_mime_type == "application/json":
        # Check if response.text is valid JSON. If not, raise ValueError to trigger retry.
        safe_json_load(response.text)

    return response


def parse_raw_input(raw_text):
    """
    Uses Gemini to intelligently split valid delimiters.
    """
    print(f"🧠 Parsing raw input for delimiters...")

    system_instruction = PROMPTS.get("parser_instruction")

    try:
        response = generate_with_retry(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[types.Content(parts=[types.Part(text=raw_text)], role="user")],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_json_schema=PARSER_SCHEMA,
            ),
        )

        print(f"🔍 RAW PARSER RESPONSE: {repr(response.text)}")
        parsed = json.loads(response.text)
        print(f"   ✅ Parsed {len(parsed)} item(s).")
        return parsed
    except Exception as e:
        print(f"   ⚠️ Parsing failed after retries: {e}. Fallback to raw text.")
        return [{"core_text": raw_text, "context_notes": ""}]


def generate_classification_prompt(active_projects_str):
    """Builds classification prompt dynamically from descriptions."""
    category_lines = []
    for cat, details in DATABASES.get("databases", {}).items():
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
    db_config = DATABASES.get("databases", {}).get(category)
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
    db_config = DATABASES.get("databases", {}).get(category)
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
