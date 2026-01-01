import functions_framework
import json
import base64
from google.genai import types

from config import PROMPTS
from clients import gemini_client, GEMINI_API_KEY
from notion_utils import (
    log_job_outcome,
    create_high_priority_task,
    append_note,
    create_cleanup_task,
)
from ai_engine import (
    parse_raw_input,
    generate_classification_prompt,
    generate_extraction_prompt,
    get_gemini_schema,
    CATEGORY_SCHEMA_CLASSIFY,
)
from external_data import enrich_context
from business_logic import (
    hydrate_dynamic_options,
    fetch_active_projects,
    fetch_inventory_map,
    fetch_trips_inventory,
    apply_business_logic,
    execute_logic,
)


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
        print(f"🔍 DEBUG: Project Prompt String: {proj_str[:100]}...")
        cat_prompt = generate_classification_prompt(proj_str)
        classify_input = (
            f"{raw_text}\n[Context: {user_context}]" if user_context else raw_text
        )

        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash-preview-09-2025",
            contents=[
                types.Content(parts=[types.Part(text=classify_input)], role="user")
            ],
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
        if project:
            print(f"   🔍 AI identified project: '{project}'")
            if project in project_id_map:
                print(f"   ✅ Exact match found: {project_id_map[project]}")
            else:
                print(
                    f"   ❌ MATCH FAILED. Available keys: {list(project_id_map.keys())}"
                )

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
            url = execute_logic(category, extracted, inventory_map, trips_id_map)

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
            full_str_for_log, category, "Success", created_url=url, ai_data=log_payload
        )

    except Exception as e:
        print(f"❌ Pipeline Error: {e}")
        log_job_outcome(
            full_str_for_log, "Unknown", "Error(s)", details=e, ai_data=log_payload
        )
        create_high_priority_task(full_str_for_log)


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

    trips_list, trips_id_map = fetch_trips_inventory()

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
