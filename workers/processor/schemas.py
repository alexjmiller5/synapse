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
            "enum": list(DATABASES.get("databases", {}).keys()),
        },
        "related_project": {"type": "string"},
    },
    "required": ["category"],
}