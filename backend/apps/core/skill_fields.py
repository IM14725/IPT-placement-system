import json

AST_EVAL_SAFE = False
try:
    import ast

    AST_EVAL_SAFE = True
except ImportError:  # pragma: no cover
    pass


def _parse_string_to_items(value):
    """Parse a single submitted string into a list of skill items.

    Handles plain comma-separated text ("Python, SQL"), JSON-encoded lists
    ('["Python", "SQL"]'), and Python-list reprs ("['Python', 'SQL']").
    Recursively unwraps values that are themselves encoded lists so that
    previously double-encoded data (e.g. ``['["[\\'Database\\']"]']``) is
    flattened back to its real items.
    """
    text = (value or "").strip()
    if not text:
        return []
    stripped = text
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return normalize_skills(parsed)
        except (ValueError, TypeError):
            pass
        if AST_EVAL_SAFE:
            try:
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, list):
                    return normalize_skills(parsed)
            except (ValueError, SyntaxError, TypeError):
                pass
    parts = []
    for part in text.split(","):
        item = part.strip().strip("\"'").strip()
        if item:
            parts.append(item)
    return parts


def normalize_skills(value):
    """Return a clean list of strings from any stored or submitted value."""
    if value is None:
        return []
    if isinstance(value, list):
        items = []
        for entry in value:
            items.extend(_parse_string_to_items(entry) if isinstance(entry, str) else normalize_skills(entry))
        return items
    if isinstance(value, (tuple, set)):
        return normalize_skills(list(value))
    if isinstance(value, str):
        return _parse_string_to_items(value)
    return []


def skills_to_text(value):
    """Render a stored skills value as a plain comma-separated string."""
    return ", ".join(normalize_skills(value))