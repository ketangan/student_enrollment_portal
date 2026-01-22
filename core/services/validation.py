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


def validate_submission(
    form: Dict[str, Any],
    post_data: Any,
    files_data: Any | None = None,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Returns: (cleaned_data, errors)
    - cleaned_data is JSON-serializable (for files we store metadata only)
    - errors maps field_key -> error message
    """
    files_data = files_data or {}
    cleaned: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    for section in form.get("sections", []):
        for field in section.get("fields", []):
            key = field["key"]
            ftype = (field.get("type") or "text").strip().lower()
            required = bool(field.get("required", False))

            # ----------------------------
            # FILE
            # ----------------------------
            if ftype == "file":
                uploaded = files_data.get(key)

                if required and not uploaded:
                    errors[key] = "This file is required."
                    continue

                if uploaded:
                    # Optional max size (MB) in YAML: max_mb: 5
                    max_mb = field.get("max_mb")
                    if max_mb:
                        try:
                            max_bytes = int(max_mb) * 1024 * 1024
                            if uploaded.size > max_bytes:
                                errors[key] = f"File too large. Max {max_mb} MB."
                                continue
                        except Exception:
                            pass

                    # Store metadata only (actual file saving happens later)
                    cleaned[key] = {
                        "original_name": getattr(uploaded, "name", ""),
                        "content_type": getattr(uploaded, "content_type", ""),
                        "size_bytes": getattr(uploaded, "size", 0),
                    }
                else:
                    cleaned[key] = None

                continue

            # ----------------------------
            # NON-FILE
            # ----------------------------
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

            if ftype == "email":
                if "@" not in str(raw_val):
                    errors[key] = "Enter a valid email address."
                    continue

            if ftype == "date":
                try:
                    datetime.strptime(str(raw_val), "%Y-%m-%d")
                except Exception:
                    errors[key] = "Enter a valid date (YYYY-MM-DD)."
                    continue

            if ftype == "number":
                try:
                    cleaned[key] = float(str(raw_val))
                except Exception:
                    errors[key] = "Enter a valid number."
                continue

            if ftype == "checkbox":
                cleaned[key] = True if raw_val in ("on", "true", "True", True) else False
                continue

            cleaned[key] = raw_val

    return cleaned, errors
