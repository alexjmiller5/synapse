from config import DATABASES

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

CATEGORY_SCHEMA_CLASSIFY = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            # Exclude helper DBs (trips/logs/youtube-channels) so the model cannot
            # even structurally classify into them — they exist only to be related to.
            "enum": [
                cat
                for cat, details in DATABASES.get("databases", {}).items()
                if not details.get("helper")
            ],
        },
        "related_project": {"type": "string"},
        "project_action": {
            "type": "string",
            "enum": ["task", "note"],
        },
    },
    "required": ["category"],
}