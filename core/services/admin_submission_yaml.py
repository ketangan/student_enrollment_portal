# core/services/admin_submission_yaml.py
from __future__ import annotations

from typing import Any

from core.admin.common import DYN_PREFIX


def build_yaml_sections(cfg, existing_data: dict[str, Any] | None, post_data=None, form: dict | None = None, school=None, form_key: str = "default") -> list[dict]:
    """
    Returns:
      yaml_sections = [
        { "title": "...", "fields": [
            {key,label,type,required,options,value}, ...
        ]},
        ...
      ]
    post_data: if provided (request.POST), its values win so page re-renders with user edits.
    school: if provided and school.program_field_key is set, DB program options replace YAML options
            for the matching field key.
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

            # 1. Resolve value from POST or existing data first.
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

            # 2. DB-driven program injection (session-aware) + bare-code normalization.
            no_programs_warning = False
            option_groups = None
            if school and getattr(school, "program_field_key", "") and key == school.program_field_key:
                from core.services.programs import has_enrollment_options, _get_enrollment_option_groups
                if not has_enrollment_options(school, form_key=form_key):
                    options = []
                    no_programs_warning = True
                else:
                    groups = _get_enrollment_option_groups(school, form_key=form_key)
                    has_named_group = any(g["label"] for g in groups)
                    if has_named_group:
                        option_groups = groups
                        options = []
                    else:
                        flat = []
                        for g in groups:
                            flat.extend(g["options"])
                        options = flat
                    # Normalize legacy bare program codes → "program:<code>" so the
                    # select value matches the namespaced option values.
                    if value and isinstance(value, str) and not (
                        value.startswith("session:") or value.startswith("program:")
                    ):
                        value = f"program:{value}"

            # 3. For select fields, resolve a human-readable display_value from options.
            #    Used by detail-page read mode; edit mode uses value directly for <option selected>.
            display_value = None
            if ftype == "select" and value:
                all_opts: list[dict] = []
                if option_groups:
                    for g in option_groups:
                        all_opts.extend(g.get("options") or [])
                else:
                    all_opts = [o for o in options if isinstance(o, dict)]
                for opt in all_opts:
                    if isinstance(opt, dict) and opt.get("value") == value:
                        display_value = opt.get("label")
                        break

            fields.append(
                {
                    "key": key,
                    "label": label,
                    "type": ftype,
                    "required": required,
                    "options": options,
                    "option_groups": option_groups,
                    "value": value,
                    "display_value": display_value,
                    # Display properties passed through for template rendering
                    "placeholder": f.get("placeholder", ""),
                    "help_text": f.get("help_text", ""),
                    "full_width": bool(f.get("full_width", False)),
                    "ui": f.get("ui", ""),
                    "text": f.get("text", ""),            # waiver body text
                    "link_url": f.get("link_url", ""),    # waiver link
                    "link_text": f.get("link_text", ""),  # waiver link label
                    "checkbox_label": f.get("checkbox_label", ""),
                    "no_programs_warning": no_programs_warning,
                }
            )

        if fields:
            yaml_sections.append({"title": section_title, "description": section.get("description", ""), "fields": fields})

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

def get_submission_workflow_filters(config_raw: dict) -> dict:
    """Returns {key: {"label": str, "statuses": list[str]}} or {} if not configured.

    Parses admin.submission_workflow.filters from school YAML.
    Schools without this block get an empty dict — callers should fall back to
    the generic status dropdown.
    """
    admin_block = (config_raw or {}).get("admin") if isinstance(config_raw, dict) else None
    if not isinstance(admin_block, dict):
        return {}
    workflow = admin_block.get("submission_workflow")
    if not isinstance(workflow, dict):
        return {}
    filters = workflow.get("filters")
    if not isinstance(filters, dict):
        return {}
    result: dict = {}
    for key, val in filters.items():
        if not isinstance(val, dict):
            continue
        label = val.get("label")
        statuses = val.get("statuses")
        if not isinstance(label, str) or not label.strip():
            continue
        if not isinstance(statuses, list) or not statuses:
            continue
        result[str(key)] = {
            "label": label.strip(),
            "statuses": [str(s) for s in statuses if s],
        }
    return result


def get_submission_workflow_transitions(config_raw: dict) -> dict:
    """Returns {from_status: [{"label": str, "status": str}, ...]} or {} if not configured.

    Parses admin.submission_workflow.transitions from school YAML.
    Skips malformed entries (non-dict actions, missing label/status keys) silently.
    Returns empty dict if the workflow block is absent — callers must not show
    inline transition buttons when this is empty.
    """
    admin_block = (config_raw or {}).get("admin") if isinstance(config_raw, dict) else None
    if not isinstance(admin_block, dict):
        return {}
    workflow = admin_block.get("submission_workflow")
    if not isinstance(workflow, dict):
        return {}
    transitions = workflow.get("transitions")
    if not isinstance(transitions, dict):
        return {}
    result: dict = {}
    for from_status, actions in transitions.items():
        if not isinstance(actions, list):
            continue
        valid_actions = []
        for action in actions:
            if not isinstance(action, dict):
                continue
            label = action.get("label")
            status = action.get("status")
            if (
                isinstance(label, str) and label.strip()
                and isinstance(status, str) and status.strip()
            ):
                valid_actions.append({"label": label.strip(), "status": status.strip()})
        if valid_actions:
            result[str(from_status)] = valid_actions
    return result


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


def get_effective_submission_status_choices(config_raw: dict, school) -> tuple[list[str], str]:
    """Like get_submission_status_choices, but for DB-program schools appends
    STATUS_ENROLLED and STATUS_WAITLISTED if not already present in the YAML list.

    This ensures admins can always manually enroll or waitlist a student even when
    the school YAML omits those statuses (auto-enrollment handles them normally).
    The local import breaks the circular dependency with views_school_common.
    """
    from core.views_school_common import STATUS_ENROLLED, STATUS_WAITLISTED  # noqa: PLC0415
    choices, default = get_submission_status_choices(config_raw)
    if getattr(school, "program_field_key", ""):
        for s in [STATUS_ENROLLED, STATUS_WAITLISTED]:
            if s not in choices:
                choices = choices + [s]
    return choices, default
