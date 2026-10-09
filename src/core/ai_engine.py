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

from core import catalog
from core.config import CATEGORIES, PROMPTS
from core.clients import get_gemini_client
from core.schemas import PARSER_SCHEMA
from core.timeutils import today_eastern
from core.workflow import binding_for, task_day

# The ONE place the model names live — overridable via env.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-latest")

# Gemini 400s (INVALID_ARGUMENT) when a response-schema enum compiles to too
# large a constrained-decoding grammar — empirically ~150+ distinct real-world
# names (2026-07: movies 'Director' hit 425, 'Famous Cast Members' 1987).
# Past this cap a field loses its enum (and its prompt options dump) instead
# of failing every capture in the category.
MAX_ENUM_OPTIONS = 100


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
    response = get_gemini_client().models.generate_content(
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
    # No '@' item separator and no '$' context separator → nothing to split.
    # Skip the LLM entirely: it exists only to split, and round-tripping text
    # it should copy verbatim has mangled it (appended '_' to
    # github.com/kunchenguid/axi, subzeroid/instagrapi, subzeroid/aiograpi).
    if "@" not in raw_text and "$" not in raw_text:
        return [{"core_text": raw_text.strip(), "context_notes": ""}]

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
    category_lines = [
        f'- "{cat}": {details.get("description", "No description.")}'
        for cat, details in CATEGORIES.items()
    ]
    return PROMPTS["categorize_template"].format(
        active_projects_list=active_projects_str,
        category_list="\n".join(category_lines),
    )


def fields(category):
    """(table, [field]) for a category: its prompts.yaml phrasing over the Soma
    catalog's contract for the column each field writes.

    The catalog supplies type, required, default and the options with their
    meanings. A field's `allowlist` narrows those options to the values Synapse
    may choose (a value the catalog lacks is dropped - the hub would reject it)
    and its instruction then owns their meaning; a field with no column (Capture
    Intent, a resolver's Title) is Synapse's own and takes its allowlist as is.
    Capture fields (`capture_columns`) edit an existing row only when the user
    asked, so they are never forced and get no default.
    """
    stanza = CATEGORIES[category]
    if category == "tasks":
        binding = binding_for("tasks") or {}
        table, columns = binding.get("table"), binding.get("columns", {})
    else:
        table = stanza.get("table")
        columns = {**stanza.get("columns", {}), **stanza.get("capture_columns", {})}
    contract = catalog.table(table)["columns"] if table else {}
    capture = set(stanza.get("capture_columns", {}))
    out = []
    for name, spec in (stanza.get("properties") or {}).items():
        spec = spec or {}
        column = contract.get(columns.get(name)) or {}
        options = column.get("options") or []
        if spec.get("allowlist") is not None:
            known = {o["v"] for o in options}
            if dropped := [v for v in spec["allowlist"] if options and v not in known]:
                print(f"   ⚠️ {category}.{name}: not catalog options, dropped: {dropped}")
            options = [{"v": v} for v in spec["allowlist"] if not options or v in known]
        out.append(
            {
                "name": name,
                "type": column.get("type") or "text",
                "required": name not in capture
                and bool(spec.get("required") or column.get("required")),
                "default": None if name in capture else column.get("default"),
                "options": options,
                "instruction": spec.get("instruction"),
            }
        )
    return table, out


def _options_block(field):
    header = f"--- VALID {field['name'].upper()} (STRICT) ---"
    if not any(o.get("d") for o in field["options"]):
        return f"{header}\n{json.dumps([o['v'] for o in field['options']])}"
    lines = [
        f"- {json.dumps(o['v'])}" + (f": {o['d']}" if o.get("d") else "") for o in field["options"]
    ]
    return "\n".join([header, *lines])


def generate_extraction_prompt(
    category,
    raw_text,
    url_context=None,
    inventory_list=None,
    user_context=None,
):
    """
    Builds extraction prompt using instructions, valid options, and contexts.
    """
    table, spec = fields(category)

    # 1. Valid Options Section (the catalog's options and their meanings)
    valid_opts_lines = [
        _options_block(f) for f in spec if f["options"] and len(f["options"]) <= MAX_ENUM_OPTIONS
    ]

    # 2. Inventory Section
    inventory_section = ""
    if inventory_list:
        inventory_section = (
            f"--- EXISTING INVENTORY (PREFER THESE NAMES) ---\n{json.dumps(inventory_list)}"
        )

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

    # 4. The store's enforced rules for this table
    rules = catalog.table(table)["rules"] if table else []
    rules_section = (
        "--- STORE RULES (a row that breaks one is rejected) ---\n"
        + "\n".join(f"- {rule}" for rule in rules)
        if rules
        else ""
    )

    # 5. Instructions Section
    # {place_tags}: the category's personal place tags (the workspace overlay's
    # tasks.place_tags, see core/workspace.py) - kept out of the committed template.
    place_tags_json = json.dumps(CATEGORIES[category].get("place_tags", []))
    extraction_day = task_day() if category == "tasks" else None
    extraction_day = extraction_day or today_eastern().isoformat()
    instr_lines = []
    for f in spec:
        parts = []
        if f["default"] is not None:
            parts.append(f"(default when the text gives none: {json.dumps(f['default'])})")
        if f["instruction"]:
            instr = f["instruction"].replace("{current_date}", extraction_day)
            instr = instr.replace("{raw_text}", raw_text)
            parts.append(instr.replace("{place_tags}", place_tags_json))
        if parts:
            instr_lines.append(f"- `{f['name']}`: " + " ".join(parts))

    return PROMPTS["extraction_template"].format(
        category=category,
        context_section=combined_context.strip(),
        valid_options_section="\n\n".join(valid_opts_lines),
        inventory_section=inventory_section,
        rules_section=rules_section,
        instructions_section="\n".join(instr_lines),
    )


def get_gemini_schema(category):
    """The extraction's JSON Schema: catalog types and options per field."""
    schema_props, required_fields = {}, []
    for f in fields(category)[1]:
        values = [o["v"] for o in f["options"]]
        string = {"type": "string"}
        if values and len(values) <= MAX_ENUM_OPTIONS:
            string["enum"] = values
        multi = f["type"] in ("multi_select", "multi_ref")
        schema_props[f["name"]] = {"type": "array", "items": string} if multi else string
        if f["required"]:
            required_fields.append(f["name"])
    return {"type": "object", "properties": schema_props, "required": required_fields}
