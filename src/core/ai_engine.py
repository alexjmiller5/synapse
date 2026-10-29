import json
import copy
import time
from email.utils import parsedate_to_datetime
from math import isfinite

import requests

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
)

from core import catalog
from core.config import CATEGORIES, PROMPTS
from core.settings import get_settings
from core.schemas import PARSER_SCHEMA
from core.timeutils import today_eastern
from core.workflow import binding_for, task_day

# Bound schema grammar size and prompt length for catalog option lists.
MAX_ENUM_OPTIONS = 100

# The value shapes of a catalog date_or_datetime column Synapse writes: a date,
# or a floating local wall-clock time (no zone) when the user states a time.
DATE_OR_DATETIME_FORMAT = (
    "Write a date as YYYY-MM-DD. Only when the user states a time of day "
    '("at noon", "3pm", "tomorrow at 9", "9:30am") write YYYY-MM-DDTHH:MM instead: '
    'the 24-hour local wall-clock time, no seconds and no time zone ("3pm" -> T15:00, '
    '"noon" -> T12:00). Never invent a time: without a stated one ("tonight", '
    '"this morning", "by end of day") the value stays date-only.'
)


def safe_json_load(text):
    """Helper to parse JSON and raise specific error if it fails.

    An empty/None model response
    raises ValueError — the retry predicate catches it — instead of letting
    json.loads(None) throw an un-retryable TypeError.
    """
    if not text or not str(text).strip():
        raise ValueError("Empty response from AI")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("Malformed JSON from AI")


def _strict_schema(schema):
    """Strict decoding represents optional values as null; callers still see absence."""
    out = copy.deepcopy(schema)
    if out.get("type") == "object":
        required = set(out.get("required", []))
        props = out.get("properties", {})
        out["properties"] = {
            name: _strict_schema(spec)
            if name in required
            else {"anyOf": [_strict_schema(spec), {"type": "null"}]}
            for name, spec in props.items()
        }
        out["required"] = list(props)
        out["additionalProperties"] = False
    elif out.get("type") == "array":
        out["items"] = _strict_schema(out["items"])
    return out


def _omit_nulls(value):
    if isinstance(value, dict):
        return {k: _omit_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_omit_nulls(v) for v in value]
    return value


def _retry_after(error):
    try:
        value = error.response.headers.get("Retry-After", "0")
    except AttributeError:
        return 0
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, delay) if isfinite(delay) else 0


def _retryable(error):
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code
        return (status >= 500 or status == 429) and _retry_after(error) <= 60
    return isinstance(error, (requests.ConnectionError, requests.Timeout, ValueError))


def _wait(state):
    return max(
        wait_exponential(multiplier=1, min=2, max=60)(state),
        _retry_after(state.outcome.exception()),
    )


@retry(retry=retry_if_exception(_retryable), wait=_wait, stop=stop_after_attempt(4), reraise=True)
def _generate(*, model, system_instruction, user_text, schema):
    settings = get_settings()
    if not settings.ai_api_key:
        raise RuntimeError("Missing AI_API_KEY")
    wrapped = schema.get("type") == "array"
    if wrapped:
        schema = {"type": "object", "properties": {"items": schema}, "required": ["items"]}
        system_instruction += '\nReturn the result array in the object field "items".'
    response = requests.post(
        f"{settings.ai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.ai_api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "synapse",
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            },
            "temperature": 0,
            "max_completion_tokens": 4096,
            "reasoning_effort": settings.ai_reasoning_effort,
        },
        timeout=60,
    )
    response.raise_for_status()
    choice = response.json()["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("Incomplete AI response")
    parsed = safe_json_load(choice["message"]["content"])
    if not isinstance(parsed, dict):
        raise ValueError("Expected an AI response object")
    if wrapped:
        parsed = parsed.get("items")
        if not isinstance(parsed, list):
            raise ValueError("Expected parsed items")
    return _omit_nulls(parsed)


def generate_with_retry(*, system_instruction, user_text, schema):
    settings = get_settings()
    kwargs = dict(system_instruction=system_instruction, user_text=user_text, schema=schema)
    try:
        return _generate(model=settings.ai_model, **kwargs)
    except requests.HTTPError as error:
        if (
            error.response.status_code == 404
            and settings.ai_fallback_model
            and settings.ai_model != settings.ai_fallback_model
        ):
            return _generate(model=settings.ai_fallback_model, **kwargs)
        raise


def parse_raw_input(raw_text):
    """
    Uses the model to intelligently split valid delimiters.
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
        parsed = generate_with_retry(
            system_instruction=system_instruction,
            user_text=raw_text,
            schema=PARSER_SCHEMA,
        )
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
        if f["type"] == "date_or_datetime":
            parts.append(DATE_OR_DATETIME_FORMAT)
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


def get_extraction_schema(category):
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
