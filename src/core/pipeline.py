import json
import re

from google.genai import types

from core.config import PROMPTS
from core.settings import get_settings
from core.notion_utils import (
    log_job_outcome,
    create_high_priority_task,
    create_cleanup_task,
    create_project_task,
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
from core.business_logic import (
    hydrate_dynamic_options,
    fetch_active_projects,
    fetch_inventory_map,
    fetch_trips_inventory,
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


def run_pipeline(
    item_data,
    project_prompts,
    project_id_map,
    inventory_map,
    inventory_list,
    trips_list,
    trips_id_map,
):
    raw_text = item_data.get("core_text", "")
    user_context = item_data.get("context_notes", "")
    full_str_for_log = f"{raw_text} (Context: {user_context})" if user_context else raw_text

    log_payload = {"Parser_Data": item_data, "Extractor_Data": None}

    try:
        print(f"🚀 Pipeline Start: {repr(raw_text)}")  # Debugging the input to the pipeline

        # 2. Classify
        # Deterministic pre-check: if the user's context says "task", it IS a task —
        # skip the classifier entirely so a movie/venue name can't hijack the category.
        # ponytail: word-match on 'task' only; widen if the prompt fix doesn't hold
        if re.search(r"\btasks?\b", user_context or "", re.IGNORECASE):
            print("⚡ Context mentions 'task' — deterministic classification: tasks")
            category = "tasks"
            # Still link a referenced project so the deterministic path doesn't drop
            # it. ponytail: case-insensitive contains match on active project names —
            # simplest correct approach; the classifier path relies on exact map keys too.
            project = _match_project(f"{raw_text} {user_context}", project_prompts)
        else:
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
            print(f"🔍 DEBUG: Response Object ID: {id(response)}")

            # 1. Check raw text value
            print(f"🔍 DEBUG: response.text is type: {type(response.text)}")
            print(f"🔍 DEBUG: response.text value: {repr(response.text)}")

            # 2. Check Finish Reason (The key indicator for blocks)
            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                print(f"🔍 DEBUG: Finish Reason: {candidate.finish_reason}")

                # 3. Check Safety Ratings (if available)
                if hasattr(candidate, "safety_ratings"):
                    print(f"🔍 DEBUG: Safety Ratings: {candidate.safety_ratings}")
            else:
                print("🔍 DEBUG: No candidates returned in response.")
            # --- VERBOSE DEBUGGING END ---

            # Check if text is None (Safety Filter Trigger)
            classified = json.loads(response.text)

            category = classified.get("category", "tasks")
            project = classified.get("related_project")
        print(f"🤖 Classification: {category}")
        # Hydrate ONLY the classified category's live Notion options (deferred
        # from startup — the extraction schema below needs _runtime_options).
        hydrate_dynamic_options(only_category=category)
        if project:
            print(f"   🔍 AI identified project: '{project}'")
            if project in project_id_map:
                print(f"   ✅ Exact match found: {project_id_map[project]}")
            else:
                print(f"   ❌ MATCH FAILED. Available keys: {list(project_id_map.keys())}")

        # 3. Extract
        url_context = (
            enrich_context(category, raw_text) or "No URL"
            if category in ["podcasts", "youtube-videos", "bookmarks", "places"]
            else None
        )
        extract_prompt = generate_extraction_prompt(
            category, raw_text, url_context, inventory_list, trips_list, user_context
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

        url = None
        project_append = False
        if project and category == "tasks":
            project_id = project_id_map.get(project)
            if project_id:
                project_append = True
                # A matched project is ALWAYS a task now (project notes removed).
                print(f"   -> Creating project task for: {project}")
                url = create_project_task(project_id, extracted)
                log_payload["Extractor_Data"]["Action"] = "Created Project Task"
            else:
                url = execute_logic(category, extracted)
        else:
            url = execute_logic(category, extracted, inventory_map, trips_id_map)

            # TMDB lookup failed for a movie/TV title — flag it for a manual fix
            # instead of trusting the AI's guessed genres/director/cast.
            if url and category in ("movies", "tv-shows") and extracted.get("_tmdb_failed"):
                title = extracted.get("Title", raw_text)
                create_cleanup_task(f"Fix metadata for: {title}", link_url=url)

            if url and url_context:
                is_scrape_error = (
                    category == "bookmarks" and "Error fetching metadata" in url_context
                )
                is_yt_error = category == "youtube-videos" and "YT Error" in url_context

                if is_scrape_error or is_yt_error:
                    print(
                        f"   🧹 Creating cleanup task for {category} failure (linked to new page)..."
                    )
                    create_cleanup_task(f"Fix Metadata for: {raw_text}", link_url=url)

        log_job_outcome(
            full_str_for_log,
            category,
            "Success",
            created_url=url,
            ai_data=log_payload,
            project_append=project_append,
        )

    except Exception as e:
        print(f"❌ Pipeline Error: {e}")
        log_job_outcome(full_str_for_log, "Unknown", "Error(s)", details=e, ai_data=log_payload)
        create_high_priority_task(full_str_for_log)


def payload_error(payload):
    """Validate a webhook payload. Returns an error message, or None if valid."""
    if not isinstance(payload, dict):
        return "Request body must be a JSON object."
    raw_text = payload.get("raw_text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return "Request must include a non-empty 'raw_text' field."
    return None


def run(payload: dict):
    print("🧠 Worker awake!")
    if not get_settings().gemini_api_key or not PROMPTS:
        print("❌ Critical: Missing API Key or Prompts")
        return

    # Option hydration is deferred to run_pipeline (only the classified
    # category) — that alone cut ~40 upfront Notion calls per thought to ~2-3.
    # Projects/inventory/trips are one query each, so they stay here.
    project_prompts, project_id_map = fetch_active_projects()
    inventory_map = fetch_inventory_map("groceries")
    inventory_list = list(inventory_map.keys())

    trips_list, trips_id_map = fetch_trips_inventory()

    try:
        full_text = payload["raw_text"]

        # DEBUG LOG: See exactly what Python sees as the pipeline input
        print(f"🔍 DEBUG INPUT REPR: {repr(full_text)}")

        # STEP 1: AI PARSING
        parsed_items = parse_raw_input(full_text)
        print(f"📋 Processing Batch: {len(parsed_items)} item(s)")

        # STEP 2: LOOP
        for item in parsed_items:
            run_pipeline(
                item,
                project_prompts,
                project_id_map,
                inventory_map,
                inventory_list,
                trips_list,
                trips_id_map,
            )

        print("--- BATCH COMPLETE ---")

    except Exception as e:
        print(f"❌ Critical Event Error: {e}")
