# core/services/admin_submission_yaml.py
from __future__ import annotations

from typing import Any

from core.admin.common import DYN_PREFIX


def build_yaml_sections(cfg, existing_data: dict[str, Any] | None, post_data=None) -> list[dict]:
    """
    Returns:
      yaml_sections = [
        { "title": "...", "fields": [
            {key,label,type,required,options,value}, ...
        ]},
        ...
      ]
    post_data: if provided (request.POST), its values win so page re-renders with user edits.
    """
    existing = existing_data or {}
    yaml_sections: list[dict] = []

    for section in (cfg.form.get("sections", []) if cfg else []):
        section_title = section.get("title") or "Form"
        fields: list[dict] = []

        for f in section.get("fields", []):
            ftype = (f.get("type") or "text").strip().lower()
            if ftype == "file":
                continue

            key = f.get("key")
            if not key:
                continue

            label = f.get("label") or key.replace("_", " ").title()
            required = bool(f.get("required", False))
            options = f.get("options") or []

            if post_data is not None:
                name = f"{DYN_PREFIX}{key}"
                if ftype == "multiselect":
                    value = post_data.getlist(name)
                elif ftype == "checkbox":
                    value = name in post_data
                else:
                    value = post_data.get(name, "")
            else:
                value = existing.get(key, "")

            fields.append(
                {
                    "key": key,
                    "label": label,
                    "type": ftype,
                    "required": required,
                    "options": options,
                    "value": value,
                }
            )

        if fields:
            yaml_sections.append({"title": section_title, "fields": fields})

    return yaml_sections


def validate_required_fields(cfg, post_data) -> list[str]:
    """
    Validates required fields from YAML against request.POST.
    Returns a list of human-friendly error strings.
    """
    errors: list[str] = []
    if not cfg:
        return errors

    for section in cfg.form.get("sections", []):
        for f in section.get("fields", []):
            ftype = (f.get("type") or "text").strip().lower()
            if ftype == "file":
                continue

            key = f.get("key")
            if not key or not f.get("required"):
                continue

            label = f.get("label") or key.replace("_", " ").title()
            name = f"{DYN_PREFIX}{key}"

            if ftype == "multiselect":
                if not post_data.getlist(name):
                    errors.append(f"{label} is required.")
            elif ftype == "checkbox":
                if name not in post_data:
                    errors.append(f"{label} is required.")
            else:
                if not (post_data.get(name, "") or "").strip():
                    errors.append(f"{label} is required.")

    return errors


def apply_post_to_submission_data(cfg, post_data, existing_data: dict) -> dict:
    """
    Applies POST dyn__ fields into a copy of existing_data and returns the new dict.
    Keeps date as string (YYYY-MM-DD), multiselect as list, checkbox as bool.
    Normalizes number to float when possible, else keeps as raw string.
    """
    data = dict(existing_data or {})
    if not cfg:
        return data

    for section in cfg.form.get("sections", []):
        for f in section.get("fields", []):
            ftype = (f.get("type") or "text").strip().lower()
            if ftype == "file":
                continue

            key = f.get("key")
            if not key:
                continue

            name = f"{DYN_PREFIX}{key}"

            if ftype == "multiselect":
                data[key] = post_data.getlist(name)
                continue

            if ftype == "checkbox":
                data[key] = name in post_data
                continue

            raw = post_data.get(name, "")
            if raw is None:
                raw = ""

            if ftype == "number":
                if raw == "":
                    data[key] = ""
                else:
                    try:
                        data[key] = float(raw)
                    except ValueError:
                        data[key] = raw
                continue

            # date + everything else: store as string
            data[key] = raw

    return data
