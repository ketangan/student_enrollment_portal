from __future__ import annotations

from typing import Any, Dict, Optional


def build_option_label_map(form: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """
    Returns a mapping: field_key -> { option_value: option_label }
    Only for select/multiselect fields.
    """
    out: Dict[str, Dict[str, str]] = {}

    for section in form.get("sections", []):
        for field in section.get("fields", []):
            ftype = field.get("type")
            if ftype not in ("select", "multiselect"):
                continue

            key = field.get("key")
            options = field.get("options") or []
            out[key] = {str(opt.get("value")): str(opt.get("label")) for opt in options}

    return out


def resolve_label(field_key: str, stored_value: Any, label_map: Dict[str, Dict[str, str]]) -> Optional[str]:
    """
    Converts a stored option value to its label if possible.
    Supports multiselect list values too.
    """
    if stored_value is None or stored_value == "":
        return None

    field_map = label_map.get(field_key, {})
    if isinstance(stored_value, list):
        labels = [field_map.get(str(v), str(v)) for v in stored_value]
        return ", ".join(labels)

    return field_map.get(str(stored_value), str(stored_value))
