# core/services/admin_submission_yaml.py
from __future__ import annotations

from typing import Any

from core.admin.common import DYN_PREFIX


def build_yaml_sections(cfg, existing_data: dict[str, Any] | None, post_data=None, form: dict | None = None) -> list[dict]:
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
    form = form or getattr(cfg, "form", None) or {}
    existing = existing_data or {}
    yaml_sections: list[dict] = []

    for section in (form.get("sections", []) if cfg else []):
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
            elif ftype == "waiver":
                value = {
                    "agreed": bool(existing.get(key, False)),
                    "timestamp": existing.get(f"{key}__at", ""),
                    "ip": existing.get(f"{key}__ip", ""),
                    "text": existing.get(f"{key}__text", ""),
                    "link_url": existing.get(f"{key}__link_url", ""),
                }
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


def validate_required_fields(
    cfg, post_data, form: dict | None = None
) -> dict[str, list[str]]:
    """
    Validates required fields from YAML against request.POST.

    Returns {"blocking": [...], "warnings": [...]}:
      - blocking: missing required field that is editable in the current admin
                  form — save must be blocked.
      - warnings: missing required field not present in the current admin form
                  (e.g. legacy YAML drift or a different multi-form step) —
                  save is allowed but admin is notified.
    """
    blocking: list[str] = []
    warnings: list[str] = []
    if not cfg:
        return {"blocking": blocking, "warnings": warnings}

    # form_cfg — the fields currently rendered in this admin view
    form_cfg = form or getattr(cfg, "form", None) or {}

    # Keys editable right now; only these can produce blocking errors
    editable_keys: set[str] = {
        f.get("key")
        for section in form_cfg.get("sections", [])
        for f in section.get("fields", [])
        if f.get("key")
    }

    # Validate against all required fields in the full YAML config so that
    # required fields from other form steps surface as warnings instead of
    # being silently ignored.
    full_form = getattr(cfg, "form", None) or form_cfg
    for section in full_form.get("sections", []):
        for f in section.get("fields", []):
            ftype = (f.get("type") or "text").strip().lower()
            if ftype in ("file", "waiver"):
                continue

            key = f.get("key")
            if not key or not f.get("required"):
                continue

            label = f.get("label") or key.replace("_", " ").title()
            name = f"{DYN_PREFIX}{key}"

            missing = False
            if ftype == "multiselect":
                missing = not post_data.getlist(name)
            elif ftype == "checkbox":
                missing = name not in post_data
            else:
                missing = not (post_data.get(name, "") or "").strip()

            if missing:
                msg = f"{label} is required."
                if key in editable_keys:
                    blocking.append(msg)
                else:
                    warnings.append(msg)

    return {"blocking": blocking, "warnings": warnings}


def apply_post_to_submission_data(cfg, post_data, existing_data: dict, form: dict | None = None) -> dict:
    """
    Applies POST dyn__ fields into a copy of existing_data and returns the new dict.
    Keeps date as string (YYYY-MM-DD), multiselect as list, checkbox as bool.
    Normalizes number to float when possible, else keeps as raw string.
    """
    data = dict(existing_data or {})
    if not cfg:
        return data

    form = form or getattr(cfg, "form", None) or {}
    for section in form.get("sections", []):
        for f in section.get("fields", []):
            ftype = (f.get("type") or "text").strip().lower()
            if ftype in ("file", "waiver"):
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

def get_submission_status_choices(config_raw: dict) -> tuple[list[str], str]:
    default_statuses = ["New", "In Review", "Contacted", "Archived"]
    default_default = "New"

    admin_block = (config_raw or {}).get("admin") if isinstance(config_raw, dict) else None
    if not isinstance(admin_block, dict):
        return default_statuses, default_default

    statuses = admin_block.get("submission_statuses")
    if isinstance(statuses, list):
        statuses = [str(s).strip() for s in statuses if str(s).strip()]
    else:
        statuses = None

    default_status = admin_block.get("default_submission_status")
    default_status = str(default_status).strip() if default_status else ""

    final_statuses = statuses or default_statuses
    final_default = default_status if default_status in final_statuses else (final_statuses[0] if final_statuses else default_default)

    return final_statuses, final_default
