import json
import os

from google.genai import types
from google.genai.errors import ClientError, ServerError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import DATABASES, PROMPTS
from core.clients import gemini_client
from core.schemas import PARSER_SCHEMA
from core.timeutils import today_eastern

# The ONE place the model names live — overridable via env.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-latest")


def safe_json_load(text):
    """Helper to parse JSON and raise specific error if it fails.

    An empty/None response (Gemini sometimes returns nothing on a safety block)
    raises ValueError — the retry predicate catches it — instead of letting
    json.loads(None) throw an un-retryable TypeError.
    """
    if not text or not str(text).strip():
        raise ValueError("Empty response from AI")
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
def _generate(model, contents, config):
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


def generate_with_retry(model, contents, config):
    """
    Robust wrapper for Gemini API calls.
    Catches 503s and Bad JSON, retrying automatically.
    On a 404 (model retired/not found), retries ONCE with GEMINI_FALLBACK_MODEL.
    """
    try:
        return _generate(model, contents, config)
    except ClientError as e:
        if getattr(e, "code", None) == 404 and model != GEMINI_FALLBACK_MODEL:
            print(f"   ⚠️ Model '{model}' not found. Retrying with '{GEMINI_FALLBACK_MODEL}'.")
            return _generate(GEMINI_FALLBACK_MODEL, contents, config)
        raise


def parse_raw_input(raw_text):
    """
    Uses Gemini to intelligently split valid delimiters.
    """
    print("🧠 Parsing raw input for delimiters...")

    system_instruction = PROMPTS.get("parser_instruction")

    try:
        response = generate_with_retry(
            model=GEMINI_MODEL,
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
        # Helper DBs (trips, logs, youtube-channels) are not classification targets;
        # they exist only to be related to by other categories.
        if details.get("helper"):
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
            formatted_instr = instr.replace("{current_date}", today_eastern().isoformat())
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

        if prop_type in ("boolean", "checkbox"):
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
            allow_new = rules.get("create_new", False)
            
            if opts and not allow_new:
                field_def = {"type": "string", "enum": opts}
            else:
                field_def = {"type": "string"}

        schema_props[prop_name] = field_def
        if rules.get("required"):
            required_fields.append(prop_name)

    return {"type": "object", "properties": schema_props, "required": required_fields}
