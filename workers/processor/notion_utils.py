import json
from datetime import date
from urllib.parse import urlparse
from config import DATABASES
from gcp_secrets import get_db_id
from clients import notion


def _notion_title(val):
    return {"title": [{"text": {"content": val}}]}


def _notion_rich_text(val):
    return (
        {"rich_text": [{"text": {"content": str(val)}}]} if val else {"rich_text": []}
    )


def _notion_multi_select(val):
    return {"multi_select": [{"name": t} for t in val]} if val else {"multi_select": []}


def _notion_date(val):
    return {"date": {"start": val}}


def _notion_status(val):
    return {"status": {"name": val}}


def _notion_select(val):
    return {"select": {"name": val}} if val else None


def _notion_url(val):
    return {"url": val}


# ==========================================
# 5. PROPERTY BUILDER (YAML-DRIVEN)
# ==========================================
def build_notion_properties(category, data):
    print(f"--- Building Props for {category} ---")
    properties = {}

    db_config = DATABASES.get("databases", {}).get(category)
    if not db_config:
        return {"Name": _notion_title(data.get("Name", "Untitled"))}

    for key, value in data.items():
        if value is None:
            continue

        prop_rules = db_config.get("properties", {}).get(key)
        if not prop_rules:
            continue

        prop_type = prop_rules.get("type")

        if prop_type == "title":
            properties[key] = _notion_title(value)
        elif prop_type == "rich_text":
            properties[key] = _notion_rich_text(value)
        elif prop_type == "rich_text_list":
            val_str = "\n".join(value) if isinstance(value, list) else str(value)
            properties[key] = _notion_rich_text(val_str)
        elif prop_type == "multi_select":
            properties[key] = _notion_multi_select(value)
        elif prop_type == "select":
            properties[key] = _notion_select(value)
        elif prop_type == "status":
            properties[key] = _notion_status(value)
        elif prop_type == "date":
            properties[key] = _notion_date(value)
        elif prop_type == "url":
            properties[key] = _notion_url(value)
    return properties


def create_page(category, props):
    # DEBUG: See the exact structure and characters sending to Notion
    print(f"🔍 DEBUG NOTION PAYLOAD REPR: {repr(props)}")

    # Build the base arguments for the API call
    body_params = {"parent": {"database_id": get_db_id(category)}, "properties": props}

    # --- ICON LOGIC ---
    if category == "podcasts":
        body_params["icon"] = {"type": "emoji", "emoji": "🎧"}
    elif category == "movies":
        body_params["icon"] = {"type": "emoji", "emoji": "🎬"}
    elif category == "tv-shows":
        body_params["icon"] = {"type": "emoji", "emoji": "📺"}
    elif category == "bookmarks":
        bookmark_url = props.get("URL", {}).get("url")
        if bookmark_url:
            parsed = urlparse(bookmark_url)
            domain = parsed.netloc or bookmark_url
            if domain and "github.com" in domain:
                # Use custom "github-light" emoji for GitHub URLs
                body_params["icon"] = {
                    "type": "custom_emoji",
                    "custom_emoji": {
                        "id": "2d103953-a8af-8072-b828-007aa3901d27"
                    },
                }
            elif domain:
                body_params["icon"] = {
                    "type": "external",
                    "external": {
                        "url": f"https://t2.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL&url=https://{domain}&size=128"
                    },
                }

    try:
        # Pass the parameters to the Notion SDK
        return notion.pages.create(**body_params)
    except Exception as e:
        print(f"❌ Notion Create Error: {e}")
        raise e


def update_status(page_id, status):
    print(f"🔄 Updating status for {page_id} to '{status}'...")
    try:
        return notion.pages.update(
            page_id=page_id, properties={"Status": {"status": {"name": status}}}
        )
    except Exception as e:
        print(f"   ❌ Update Status Failed: {e}")
        return None


def append_note(page_id, text):
    print(f"📎 Appending note to {page_id}...")

    def get_utf16_split(content, limit=2000):
        chunks = []
        current_chunk = []
        current_len = 0

        for char in content:
            char_len = len(char.encode("utf-16-le")) // 2

            if current_len + char_len > limit:
                chunks.append("".join(current_chunk))
                current_chunk = []
                current_len = 0

            current_chunk.append(char)
            current_len += char_len

        if current_chunk:
            chunks.append("".join(current_chunk))
        return chunks

    try:
        page = notion.pages.retrieve(page_id)
        current_notes = page["properties"].get("Notes", {}).get("rich_text", [])

        safe_notes = []

        for note_obj in current_notes:
            content = note_obj.get("text", {}).get("content", "")
            anns = note_obj.get("annotations", {})

            chunks = get_utf16_split(content)
            for chunk in chunks:
                safe_notes.append(
                    {"type": "text", "text": {"content": chunk}, "annotations": anns}
                )

        new_chunks = get_utf16_split(f"{text}")
        for chunk in new_chunks:
            safe_notes.append(
                {
                    "type": "text",
                    "text": {
                        "content": chunk if chunk != new_chunks[0] else f"\n{chunk}"
                    },
                }
            )

        notion.pages.update(
            page_id=page_id, properties={"Notes": {"rich_text": safe_notes}}
        )
        print("   ✅ Note appended successfully.")

    except Exception as e:
        print(f"   ❌ Append Note Failed: {e}")


def create_project_task(project_id, extracted_data):
    """
    Creates a Task in the Tasks DB and links it to a Project via relation.
    """
    print(f"📋 Creating project task linked to project {project_id}...")
    props = build_notion_properties("tasks", extracted_data)

    # Link to project via relation
    props["Project"] = {"relation": [{"id": project_id}]}

    resp = create_page("tasks", props)
    url = resp.get("url")
    print(f"   ✅ Project task created: {url}")
    return url


def create_project_note(project_id, note_text):
    """
    Creates a Note in the Notes DB and links it to a Project via relation.
    """
    print(f"📝 Creating project note linked to project {project_id}...")

    props = {
        "Title": _notion_title(note_text),
        "Project": {"relation": [{"id": project_id}]},
        "Status": _notion_status("Reference"),
    }

    db_id = get_db_id("notes")
    resp = notion.pages.create(
        parent={"database_id": db_id}, properties=props
    )
    url = resp.get("url")
    print(f"   ✅ Project note created: {url}")
    return url


def create_cleanup_task(desc, link_url=None):
    print(f"🧹 Creating cleanup task: {desc}")
    props = {
        "Name": _notion_title(desc),
        "AI Title": _notion_rich_text(desc),
        "Status": _notion_status("To Do"),
        "Tags": _notion_multi_select(["Chore"]),
        "Due Date": _notion_date(date.today().isoformat()),
        "Priority": _notion_select("Low"),
    }

    # If we have a link, add it to the 'Links' property
    if link_url:
        props["Links"] = _notion_rich_text(link_url)

    try:
        create_page("tasks", props)
    except Exception as e:
        print(f"   ❌ Cleanup Task Creation Failed: {e}")


def create_high_priority_task(desc, link_url=None):
    print(f"🧹 Creating cleanup task: {desc}")
    classification_message = (
        "Classify the following thought (it failed due to pipeline errors): "
    )
    task_text = f"{classification_message}{desc}"
    props = {
        "Name": _notion_title(task_text),
        "AI Title": _notion_rich_text(task_text),
        "Status": _notion_status("To Do"),
        "Tags": _notion_multi_select(["Chore"]),
        "Due Date": _notion_date(date.today().isoformat()),
        "Priority": _notion_select("High"),
    }

    # If we have a link, add it to the 'Links' property
    if link_url:
        props["Links"] = _notion_rich_text(link_url)

    try:
        create_page("tasks", props)
    except Exception as e:
        print(f"   ❌ Cleanup Task Creation Failed: {e}")


def log_job_outcome(
    raw_text, category, status, details="", created_url=None, ai_data=None
):
    print(f"--- Logging: {status} ---")
    log_id = get_db_id("logs")
    if not notion or not log_id:
        return

    ai_summary_text = ""
    if ai_data:
        try:
            ai_summary_text = json.dumps(ai_data, indent=2, ensure_ascii=False)[:2000]
        except:
            ai_summary_text = str(ai_data)[:2000]

    props = {
        "Raw Input": _notion_title(raw_text[:2000]),
        "Code Execution": _notion_status(status),
        "Category": _notion_select(category),
        "Error Details": _notion_rich_text(str(details)[:2000]),
        "AI Summary": _notion_rich_text(ai_summary_text),
    }
    if created_url:
        props["Created Item"] = {"url": created_url}
    try:
        notion.pages.create(parent={"database_id": log_id}, properties=props)
    except Exception as e:
        print(f"Log failed: {e}")


def fetch_existing_page(category, value, key="Name"):
    db_id = get_db_id(category)
    if not notion or not db_id:
        return None

    # 1. Clean the search term for better matching
    clean_val = value.replace("The ", "").strip()

    try:
        print(f"🔍 Searching {category} for '{value}' (Smart Search: '{clean_val}')...")

        # FIX: Use raw notion.request() to bypass SDK version issues
        resp = notion.request(
            path=f"databases/{db_id}/query",
            method="POST",
            body={"filter": {"property": key, "title": {"contains": clean_val}}},
        )

        results = resp.get("results", [])

        if results:
            found_page = results[0]

            # Safely extract title
            title_prop = found_page["properties"].get(key, {}).get("title", [])
            found_title = title_prop[0]["plain_text"] if title_prop else "Unknown"
            found_id = found_page["id"]

            print(f"   ✅ Found match: '{found_title}' (ID: {found_id})")
            return found_id

        print(f"   🔸 No match found for '{clean_val}'")
    except Exception as e:
        print(f"   ❌ Search failed for '{value}': {e}")
    return None
