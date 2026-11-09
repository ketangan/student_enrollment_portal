"""Readable fallback summaries for the shared school record timeline."""

from django import template

register = template.Library()


@register.filter
def record_audit_summary(entry, record="record"):
    # Detailed email/status events are rendered by the existing timeline. Avoid
    # exposing storage field names or claiming an outcome absent from the audit.
    extra = entry.extra if isinstance(entry.extra, dict) else {}
    if extra.get("name") == "submission_update":
        fields = extra.get("fields", [])
        if fields == ["internal_notes"]:
            return "updated internal notes"
        return "updated application details"
    if entry.action == "add":
        return "submitted application" if record == "application" else f"created {record}"
    if entry.action == "delete":
        return f"deleted {record}"
    if entry.action == "change":
        return f"updated {record} details"
    return "recorded an activity"
