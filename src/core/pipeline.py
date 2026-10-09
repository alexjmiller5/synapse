import json
import re

from google.genai import types

from core.settings import get_settings
from core.workflow import (
    binding_for,
    capture_scope,
    create_cleanup_task,
    create_high_priority_task,
    create_task,
    current_capture,
    log_execution,
    prepare_item,
)
from core.ai_engine import (
    GEMINI_MODEL,
    generate_with_retry,
    parse_raw_input,
    generate_classification_prompt,
    generate_extraction_prompt,
    get_gemini_schema,
)
from core.schemas import CATEGORY_SCHEMA_CLASSIFY

from core.external_data import enrich_context
from core.handlers import Failed
from core.business_logic import (
    fetch_active_projects,
    fetch_inventory_map,
    apply_business_logic,
    execute_logic,
)


def _match_project(text, project_names):
    """Return the first active project whose name appears (case-insensitive) in text.

    ponytail: plain contains-match — enough to re-link a project the deterministic
    'task' path would otherwise drop; upgrade to fuzzy matching only if it misses.
    """
    haystack = (text or "").lower()
    for name in project_names or []:
        if name.lower() in haystack:
            return name
    return None


def _classify_with_ai(raw_text, user_context, project_prompts):
    """One classifier call; returns the parsed {'category', 'related_project'} dict."""
    proj_str = ", ".join(project_prompts) if project_prompts else "None"
    print(f"🔍 DEBUG: Project Prompt String: {proj_str[:100]}...")
    cat_prompt = generate_classification_prompt(proj_str)
    classify_input = f"{raw_text}\n[Context: {user_context}]" if user_context else raw_text

    response = generate_with_retry(
        model=GEMINI_MODEL,
        contents=[types.Content(parts=[types.Part(text=classify_input)], role="user")],
        config=types.GenerateContentConfig(
            system_instruction=cat_prompt,
            response_mime_type="application/json",
            response_json_schema=CATEGORY_SCHEMA_CLASSIFY,
        ),
    )

    # --- VERBOSE DEBUGGING START ---
    print(f"🔍 DEBUG: response.text value: {repr(response.text)}")
    if response.candidates and len(response.candidates) > 0:
        print(f"🔍 DEBUG: Finish Reason: {response.candidates[0].finish_reason}")
    else:
        print("🔍 DEBUG: No candidates returned in response.")
    # --- VERBOSE DEBUGGING END ---

    return json.loads(response.text)


# `pj` anywhere in the capture (text or context) = "this is a project task": the
# category is forced to tasks and a project is always linked. The token itself
# is stripped so it never lands in the task name.
PJ_KEYWORD = re.compile(r"\bpj\b", re.IGNORECASE)


def _strip_pj(text):
    return re.sub(r"\s{2,}", " ", PJ_KEYWORD.sub("", text or "")).strip()


def run_pipeline(
    item_data,
    project_prompts,
    project_id_map,
    inventory_map,
    inventory_list,
    source=None,
):
    raw_text = item_data.get("core_text", "")
    user_context = item_data.get("context_notes", "")
    full_str_for_log = f"{raw_text} (Context: {user_context})" if user_context else raw_text
    force_project = bool(PJ_KEYWORD.search(f"{raw_text} {user_context}"))
    if force_project:
        print("⚡ 'pj' keyword — forcing a project task")
        raw_text, user_context = _strip_pj(raw_text), _strip_pj(user_context)

    log_payload = {"Parser_Data": item_data, "Extractor_Data": None}

    def prepare():
        print(f"🚀 Pipeline Start: {repr(raw_text)}")  # Debugging the input to the pipeline

        # 2. Classify
        # Deterministic pre-check: if the user's context says "task", it IS a task —
        # skip the classifier entirely so a movie/venue name can't hijack the category.
        # ponytail: word-match on 'task' only; widen if the prompt fix doesn't hold
        if force_project or re.search(r"\btasks?\b", user_context or "", re.IGNORECASE):
            print("⚡ Context mentions 'task' — deterministic classification: tasks")
            category = "tasks"
            # Still link a referenced project so the deterministic path doesn't drop
            # it. ponytail: case-insensitive contains match on active project names —
            # simplest correct approach; the classifier path relies on exact map keys too.
            project = _match_project(f"{raw_text} {user_context}", project_prompts)
            if not project and (
                force_project
                or re.search(r"\bproj\w*", f"{raw_text} {user_context}", re.IGNORECASE)
            ):
                # The text names a project the contains-match couldn't find (typo
                # "burdown", paraphrase "file renaming convention proj") — ask the
                # classifier just for the project: it sees the exact active-project
                # list, so it returns exact map keys. Category stays tasks.
                # ponytail: gated on the word "proj*" so plain task captures skip
                # the extra LLM call; widen the gate if a rename slips through.
                print("   🔁 No contains-match but 'proj' mentioned — classifier rescue")
                project = _classify_with_ai(raw_text, user_context, project_prompts).get(
                    "related_project"
                )
        else:
            classified = _classify_with_ai(raw_text, user_context, project_prompts)
            category = classified.get("category", "tasks")
            project = classified.get("related_project")
        print(f"🤖 Classification: {category}")
        if project:
            print(f"   🔍 AI identified project: '{project}'")
            if project in project_id_map:
                print(f"   ✅ Exact match found: {project_id_map[project]}")
            else:
                print(f"   ❌ MATCH FAILED. Available keys: {list(project_id_map.keys())}")

        # 3. Extract
        url_context = (
            enrich_context(category, raw_text) or "No URL"
            if category in ["podcasts", "youtube-videos", "bookmarks", "articles"]
            else None
        )
        extract_prompt = generate_extraction_prompt(
            category, raw_text, url_context, inventory_list, user_context
        )

        # DEBUG: Capture Raw AI Response before JSON Load
        ai_response_obj = generate_with_retry(
            model=GEMINI_MODEL,
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

        # 4. Execute
        if not extracted:
            print("   ⚠️ Extraction returned empty.")
            extracted = {"Name": raw_text}

        # source_text grounds the tasks Name back to the user's verbatim input
        # (apply_business_logic overrides only for the tasks category).
        extracted = apply_business_logic(category, extracted, project, raw_text)
        log_payload["Extractor_Data"] = extracted

        return {
            "category": category,
            "project": project,
            "extracted": extracted,
            "log_payload": log_payload,
            "url_context": url_context,
        }

    prepared = prepare_item(prepare)
    if "preparation_error" in prepared:
        recovery = create_high_priority_task(full_str_for_log)
        log_execution(
            full_str_for_log,
            "Unknown",
            "Error(s)",
            details=prepared["preparation_error"],
            created_url=recovery,
            ai_data=log_payload,
            source=source,
        )
        return
    category, project = prepared["category"], prepared["project"]
    extracted, log_payload = prepared["extracted"], prepared["log_payload"]
    url_context = prepared["url_context"]

    def execute():
        url = None
        project_append = False
        if project and category == "tasks" and project_id_map.get(project):
            # A matched project is ALWAYS a task (project notes removed).
            print(f"   -> Creating project task for: {project}")
            project_append = True
            url = create_task(extracted, project_id_map[project])
            log_payload["Extractor_Data"]["Action"] = "Created Project Task"
        else:
            if category == "bookmarks" and "Error fetching metadata" in (url_context or ""):
                # The page was never read, so everything but the URL is the model's
                # guess. Title stays unset (the stanza's review_if_missing flags the
                # row); Description and Tags only fill gaps on an already-known url.
                extracted.pop("Title", None)
                extracted["_fill_only"] = ["Description", "Tags"]
            url = execute_logic(category, extracted, inventory_map)

            if url and category == "youtube-videos" and "YT Error" in (url_context or ""):
                print("   🧹 Creating cleanup task for youtube-videos failure...")
                create_cleanup_task(f"Fix Metadata for: {raw_text}", link_url=url)

        # A handler that wrote nothing returns Failed - never log that as Success.
        outcome, details = "Success", ""
        if isinstance(url, Failed):
            outcome, details, url = "Error(s)", url.detail, None

        return {
            "url": url,
            "outcome": outcome,
            "details": details,
            "project_append": project_append,
            "log_payload": log_payload,
        }

    # A remote effect may commit before a failure: the frozen result means a
    # Modal retry resumes here instead of writing a second task.
    active = current_capture()
    result = active.journal.checkpoint(f"item/{active.item_index}/result", execute)
    log_execution(
        full_str_for_log,
        category,
        result["outcome"],
        details=result["details"],
        created_url=result["url"],
        ai_data=result["log_payload"],
        project_append=result["project_append"],
        source=source,
    )


def payload_error(payload):
    """Validate a webhook payload. Returns an error message, or None if valid."""
    if not isinstance(payload, dict):
        return "Request body must be a JSON object."
    raw_text = payload.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return "Request must include a non-empty 'raw_text' field."
    source = payload.get("source")
    if source is not None and (not isinstance(source, str) or len(source) > MAX_SOURCE_LEN):
        return f"'source' must be a string of at most {MAX_SOURCE_LEN} characters."
    if "capture_id" in payload:
        from core.workflow import accepted_capture

        try:
            accepted_capture(payload, "validation")
        except ValueError as error:
            return str(error)
    ws = payload.get("workspace")
    if ws is not None and (not isinstance(ws, str) or not WORKSPACE_ID.fullmatch(ws)):
        return "'workspace' must be a workspace id (lowercase letters, digits, dashes)."
    return None


WORKSPACE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")


# Where a capture came from (an app, a named shortcut, a hotkey, an agent) — a
# free-form caller-chosen label logged on the execution; never parsed.
MAX_SOURCE_LEN = 64


def _run(payload: dict):
    """Process one capture inside its journal (core.workflow.capture_scope)."""
    active = current_capture()
    print("🧠 Worker awake!")
    if not get_settings().gemini_api_key:
        raise RuntimeError("Missing GEMINI_API_KEY")

    def load_context():
        names, ids = fetch_active_projects()
        return {
            "projects": names,
            "project_ids": ids,
            "inventory": fetch_inventory_map("groceries"),
        }

    context = active.journal.checkpoint("context", load_context)
    project_prompts, project_id_map = context["projects"], context["project_ids"]
    inventory_map = context["inventory"]
    inventory_list = list(inventory_map.keys())

    full_text = payload["raw_text"]
    print(f"🔍 DEBUG INPUT REPR: {repr(full_text)}")

    # STEP 1: AI PARSING
    parsed_items = active.journal.checkpoint("parsed_items", lambda: parse_raw_input(full_text))
    print(f"📋 Processing Batch: {len(parsed_items)} item(s)")

    # STEP 2: LOOP
    for index, item in enumerate(parsed_items):
        active.item_index = index
        active.counters = {}
        run_pipeline(
            item,
            project_prompts,
            project_id_map,
            inventory_map,
            inventory_list,
            source=payload.get("source"),
        )
    print("--- BATCH COMPLETE ---")


def run(payload: dict, store):
    """Process one accepted capture: tasks and the execution log are Soma rows
    (workflow.tasks / workflow.executions), journaled under its capture_id in
    `store`, so a Modal retry resumes without a second write."""
    if binding_for("tasks") is None or binding_for("executions") is None:
        raise ValueError("A workspace needs workflow.tasks and workflow.executions bindings")
    if "capture_id" not in payload:
        raise ValueError("A capture needs its persisted capture_id")
    with capture_scope(store, payload) as active:
        if active.journal.completed:
            return
        _run(payload)
        active.journal.finish()
