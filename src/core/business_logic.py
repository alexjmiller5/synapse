from core.config import DATABASES
from core.secrets import get_db_id
from core.clients import get_notion
from core.timeutils import today_eastern
from core.notion_utils import clean_text, prop_id
from core.handlers import (
    handle_hub_logic,
    handle_url_media,
    handle_youtube_logic,
    handle_movies_tv_logic,
    handle_people_logic,
    handle_default_logic,
)
from core.life_hub import pull_rows


def query_notion_db(category_key, query_body=None):
    """Generic helper to safely query a Notion database."""
    db_id = get_db_id(category_key)
    if not get_notion() or not db_id:
        return []

    if query_body is None:
        query_body = {"page_size": 100}

    try:
        resp = get_notion().request(path=f"databases/{db_id}/query", method="POST", body=query_body)
        return resp.get("results", [])
    except Exception as e:
        print(f"❌ Failed to query {category_key}: {e}")
        return []


def fetch_inventory_map(category):
    """{item name: row id} for a hub-backed category, from the hub's live rows.

    The names go into the extraction prompt so a capture reuses an existing
    spelling; the handler matches on the same column when it writes.
    """
    print(f"📚 Fetching full inventory for {category}...")
    stanza = DATABASES["databases"][category]
    key = stanza["match_on"]
    try:
        rows = pull_rows(stanza["hub_table"], [key])
    except Exception as e:
        print(f"   ⚠️ Inventory unavailable: {e}")
        return {}
    inventory = {r[key]: r["id"] for r in rows if r.get(key)}
    print(f"   ✅ Loaded {len(inventory)} items.")
    return inventory


# databases.yaml property type -> the Notion property type it maps to (for validation).
_YAML_TO_NOTION_TYPE = {
    "title": "title",
    "rich_text": "rich_text",
    "rich_text_list": "rich_text",
    "select": "select",
    "multi_select": "multi_select",
    "status": "status",
    "date": "date",
    "url": "url",
    "relation": "relation",
    "checkbox": "checkbox",
}


def _options_from_prop(prop):
    """Option names for a live select/multi_select/status property (else [])."""
    t = prop.get("type")
    if t in ("select", "multi_select", "status"):
        return [o["name"] for o in prop.get(t, {}).get("options", [])]
    return []


def fetch_db_schema(db_id):
    """Live property schema {name: prop_object} for a DB — one Notion call.
    Returns {} on no-client / no-id / error."""
    if not get_notion() or not db_id:
        return {}
    try:
        return get_notion().databases.retrieve(db_id).get("properties", {})
    except Exception as e:
        print(f"   ❌ Schema fetch failed for {db_id}: {e}")
        return {}


def _find_prop(schema, category, prop_name):
    """Locate a property in a live schema by stable id (rename-safe), name fallback."""
    target_id = prop_id(category, prop_name) if category else prop_name
    return next((p for p in schema.values() if p.get("id") == target_id), None) or schema.get(
        prop_name
    )


def fetch_property_options(db_id, prop_name, category=None, schema=None):
    """Live options for a select/status/multi_select property. Pass a pre-fetched
    schema to avoid a redundant retrieve (hydration fetches once per category)."""
    if schema is None:
        schema = fetch_db_schema(db_id)
    prop = _find_prop(schema, category, prop_name)
    return _options_from_prop(prop) if prop else []


def validate_category(category, details, schema):
    """Drift issues between a category's databases.yaml stanza and the live Notion
    schema: missing properties, type mismatches, allowlist options not in Notion."""
    issues = []
    if not schema:
        return [f"{category}: could not fetch live schema"]
    for name, rules in details.get("properties", {}).items():
        ytype = rules.get("type")
        if ytype == "ignore":
            continue
        live = _find_prop(schema, category, name)
        if not live:
            issues.append(f"{category}.{name}: not found in Notion (id={prop_id(category, name)})")
            continue
        expected = _YAML_TO_NOTION_TYPE.get(ytype, ytype)
        if live.get("type") != expected:
            issues.append(
                f"{category}.{name}: Notion type '{live.get('type')}' != expected '{expected}'"
            )
        allowlist = rules.get("allowlist")
        if allowlist:
            missing = [o for o in allowlist if o not in _options_from_prop(live)]
            if missing:
                issues.append(
                    f"{category}.{name}: allowlist options not in Notion select: {missing}"
                )
    return issues


def validate_all():
    """Validate EVERY (non-helper) category's databases.yaml against live Notion.
    Returns {category: [issues]} for categories with drift (empty dict = all good)."""
    report = {}
    for category, details in DATABASES.get("databases", {}).items():
        if details.get("helper") or details.get("hub_table"):
            continue  # a life-data table has no live Notion schema to drift from
        db_id = get_db_id(category)
        if not db_id:
            report[category] = ["no db_id configured"]
            continue
        issues = validate_category(category, details, fetch_db_schema(db_id))
        if issues:
            report[category] = issues
    return report


def hydrate_dynamic_options(only_category=None):
    """Load live Notion select/status options into each category's schema AND
    validate that category against the live structure (free — same schema fetch).

    Pass only_category to hydrate a SINGLE category (the classified one) — the hot
    path. One `databases.retrieve` per category (not per property).
    """
    print(f"🔄 Hydrating Options{f' for {only_category}' if only_category else ''}...")
    from core.workflow import binding_for

    for category, details in DATABASES.get("databases", {}).items():
        if category == "tasks" and binding_for("tasks") is not None:
            continue
        if only_category and category != only_category:
            continue
        if details.get("helper") or details.get("hub_table"):
            continue  # hub_table = a life-data table: no live Notion options to read
        db_id = get_db_id(category)
        if not db_id:
            print(f"   ⚠️ Skipping {category} (No DB ID)")
            continue

        schema = fetch_db_schema(db_id)  # one fetch, reused for hydrate + validate

        # Per-execution config-drift check (databases.yaml vs live Notion).
        for issue in validate_category(category, details, schema):
            print(f"   ⚠️ VALIDATE: {issue}")

        for prop_name, rules in details.get("properties", {}).items():
            if rules.get("type") not in ["select", "multi_select", "status"]:
                continue

            real_options = fetch_property_options(db_id, prop_name, category, schema=schema)
            allowlist = rules.get("allowlist")
            final_options = (
                [opt for opt in real_options if opt in allowlist] if allowlist else real_options
            )

            rules["_runtime_options"] = final_options
            print(
                f"   🔹 {category} [{prop_name}]: Loaded {len(final_options)} options: {final_options}"
            )
    print("✅ Hydration complete.")


def fetch_active_projects():
    """
    Fetches active projects from the dedicated Projects database.
    Returns:
    - prompt_list: ["Project Name", ...]
    - id_map: {"Project Name": "page-id"}
    """
    from core.workflow import active_projects, binding_for

    binding = binding_for("projects")
    if binding is not None:
        return active_projects(binding)

    print("📂 Fetching active projects from Projects DB...")

    query_body = {
        "filter": {
            "or": [
                {"property": "Status", "status": {"equals": "To Do"}},
                {"property": "Status", "status": {"equals": "In progress"}},
            ]
        },
        "page_size": 100,
    }

    results = query_notion_db("projects", query_body)

    prompt_list = []
    id_map = {}

    for p in results:
        try:
            props = p["properties"]
            page_id = p["id"]

            # Extract project title
            title_prop = props.get("Title", {}).get("title", [])
            name = title_prop[0]["plain_text"] if title_prop else "Unknown"

            if name == "Unknown":
                continue

            id_map[name] = page_id
            prompt_list.append(name)

            print(f"   👉 Loaded Project: '{name}' (ID: {page_id})")

        except Exception as e:
            print(f"   ⚠️ Skipping a project due to error: {e}")
            continue

    print(f"✅ Total Active Projects Loaded: {len(prompt_list)}")
    return prompt_list, id_map


def apply_business_logic(category, data, related_project=None, source_text=None):
    today_str = today_eastern().isoformat()

    if category == "tasks":
        data["Status"] = "To Do"
        # Tasks default to High priority (project-routed tasks included) —
        # the AI usually sets this, but never rely on it.
        data.setdefault("Priority", "High")
        # Grounding guard: a task Name MUST be the user's verbatim text
        # (databases.yaml says so), but the AI sometimes rewrites/hallucinates it.
        # Force it back to the original input. ONLY tasks — other categories
        # legitimately transform their title (groceries Title-Cases, movies
        # correct titles). clean_text also runs at the write choke-point; applied
        # here too so the grounded value is clean wherever data is read.
        if source_text is not None:
            data["Name"] = clean_text(source_text)
        # Place-tagged tasks (workspace tasks.place_tags) are dateless by design: the
        # prompt returns "" when no date was given — drop it so an empty date
        # payload never reaches Notion.
        if not data.get("Due Date"):
            data.pop("Due Date", None)
        if related_project:
            annotation = f"Project: {related_project}"
            existing = data.get("Notes") or ""
            data["Notes"] = f"{existing}\n\n{annotation}" if existing else annotation

    elif category == "podcasts":
        if data.get("Status") == "Finished":
            data["Date Listened To"] = today_str

    elif category == "bookmarks":
        # House style the catalog enforces: no trailing period, Github tag on
        # github.com urls - applied here so the row is valid before the push.
        if isinstance(data.get("Description"), str):
            data["Description"] = data["Description"].rstrip(".")
        if "github.com" in data.get("URL", ""):
            tags = data.get("Tags", [])
            if isinstance(tags, list) and "Github" not in tags:
                tags.append("Github")
                data["Tags"] = tags

    return data


LOGIC_HANDLERS = {
    "youtube-videos": handle_youtube_logic,
    "movies": handle_movies_tv_logic,
    "tv-shows": handle_movies_tv_logic,
    "people": handle_people_logic,
}


def execute_logic(category, data, inventory_map=None):
    from core.workflow import binding_for, create_task

    if category == "tasks" and binding_for("tasks") is not None:
        return create_task(data)
    print(f"⚙️ Executing Logic for: {category}")
    stanza = DATABASES["databases"].get(category, {})
    if category in ("podcasts", "articles"):
        return handle_url_media(category, data)
    if stanza.get("hub_table") and "columns" in stanza:
        return handle_hub_logic(category, data)
    handler = LOGIC_HANDLERS.get(category, handle_default_logic)
    return handler(category, data)
