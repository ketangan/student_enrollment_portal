from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def validate_submission(form: Dict[str, Any], post_data: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Returns: (cleaned_data, errors)
    - cleaned_data is JSON-serializable
    - errors maps field_key -> error message
    """
    cleaned: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    for section in form.get("sections", []):
        for field in section.get("fields", []):
            key = field["key"]
            ftype = field["type"]
            required = bool(field.get("required", False))

            raw_val = post_data.get(key)

            # multiselect comes as list
            if ftype == "multiselect":
                raw_val = post_data.getlist(key)  # type: ignore[attr-defined]

            if required and _is_empty(raw_val):
                errors[key] = "This field is required."
                continue

            if _is_empty(raw_val):
                cleaned[key] = raw_val if ftype == "multiselect" else ""
                continue

            # basic type validations
            if ftype == "email":
                if "@" not in str(raw_val):
                    errors[key] = "Enter a valid email address."
                    continue

            if ftype == "date":
                try:
                    # HTML date input posts YYYY-MM-DD
                    datetime.strptime(str(raw_val), "%Y-%m-%d")
                except Exception:
                    errors[key] = "Enter a valid date (YYYY-MM-DD)."
                    continue

            if ftype == "number":
                try:
                    cleaned_number = float(str(raw_val))
                except Exception:
                    errors[key] = "Enter a valid number."
                    continue
                cleaned[key] = cleaned_number
                continue

            if ftype == "checkbox":
                # checkbox posts "on" if checked, missing if unchecked
                cleaned[key] = True if raw_val in ("on", "true", "True", True) else False
                continue

            # default texty types
            cleaned[key] = raw_val

    return cleaned, errors
