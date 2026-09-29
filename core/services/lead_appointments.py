"""Opt-in appointment display for school-configured lead select fields."""


def can_schedule_lead_appointment(lead) -> bool:
    return not lead.converted_submission_id and lead.status in {"new", "contacted", "trial_scheduled"}


def get_appointment_field(lead_config: dict) -> dict | None:
    key = lead_config.get("appointment_field")
    if not isinstance(key, str) or not key:
        return None
    return next((
        field for field in lead_config.get("fields", [])
        if isinstance(field, dict) and field.get("key") == key
        and field.get("type") == "select"
    ), None)


def appointment_value_is_valid(field: dict, value: str) -> bool:
    return any(
        isinstance(option, dict) and option.get("value") == value
        for option in field.get("options", [])
    )


def appointment_display(field: dict, data: dict) -> str:
    fields = data.get("form_fields", {}) if isinstance(data, dict) else {}
    value = fields.get(field["key"], "") if isinstance(fields, dict) else ""
    for option in field.get("options", []):
        if isinstance(option, dict) and option.get("value") == value:
            return option.get("label") or str(value)
    # Keep historical appointments visible even after available slots change.
    return str(value) if value else ""
