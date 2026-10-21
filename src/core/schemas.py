from core.config import CATEGORIES

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
            "enum": list(CATEGORIES),
        },
        "related_project": {"type": "string"},
    },
    "required": ["category"],
}


def classification_schema():
    """Choose from the active workspace's categories at call time."""
    return {
        **CATEGORY_SCHEMA_CLASSIFY,
        "properties": {
            **CATEGORY_SCHEMA_CLASSIFY["properties"],
            "category": {"type": "string", "enum": list(CATEGORIES)},
        },
    }
