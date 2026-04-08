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
    partial: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Returns: (cleaned_data, errors)
    - cleaned_data is JSON-serializable (for files we store metadata only)
    - errors maps field_key -> error message
    - partial=True: skip required-field enforcement (used for draft saves).
      All other validation (type coercion, format checks) still runs.
      File fields are skipped entirely in partial mode (not preserved in drafts).
    """
    files_data = files_data or {}
    cleaned: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    for section in form.get("sections", []):
        for field in section.get("fields", []):
            key = field["key"]
            ftype = (field.get("type") or "text").strip().lower()
            required = bool(field.get("required", False))

            # ✅ FILE: validate from FILES, not POST
            if ftype == "file":
                if partial:
                    continue  # file fields not stored in drafts; re-upload on final submit

                uploaded = files_data.get(key)

                if required and not uploaded:
                    errors[key] = "This file is required."
                    continue

                if uploaded:
                    max_mb = field.get("max_mb")
                    if max_mb:
                        try:
                            max_bytes = int(max_mb) * 1024 * 1024
                            if uploaded.size > max_bytes:
                                errors[key] = f"File too large. Max {max_mb} MB."
                                continue
                        except Exception:
                            pass

                    cleaned[key] = {
                        "original_name": getattr(uploaded, "name", ""),
                        "content_type": getattr(uploaded, "content_type", ""),
                        "size_bytes": getattr(uploaded, "size", 0),
                    }
                else:
                    cleaned[key] = None

                continue

            # --- non-file fields (existing logic) ---
            raw_val = post_data.get(key)

            if ftype == "multiselect":
                raw_val = post_data.getlist(key)  # type: ignore[attr-defined]

            if ftype == "waiver":
                agreed = raw_val in ("on", "true", "True", True)
                if required and not agreed and not partial:
                    errors[key] = "You must agree to continue."
                    continue
                cleaned[key] = agreed
                continue

            if required and not partial and _is_empty(raw_val):
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
