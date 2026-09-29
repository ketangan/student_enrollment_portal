"""Opt-in admissions display state, derived without changing stored lead statuses."""
from django.db.models import Case, CharField, F, Value, When


APPLICATION_SUBMITTED = "application_submitted"


def requires_enrollment_approval(raw):
    admin = (raw or {}).get("admin", {})
    return isinstance(admin, dict) and admin.get("require_enrollment_approval") is True


def with_admissions_status(queryset, raw):
    if not requires_enrollment_approval(raw):
        return queryset
    # Legacy conversion stores "enrolled" on the lead. For approval workflows,
    # the linked, same-school application owns the actual enrollment decision.
    return queryset.annotate(admissions_status=Case(
        When(status="lost", then=F("status")),
        When(converted_submission__school_id=F("school_id"),
             converted_submission__status="Enrolled", then=Value("enrolled")),
        When(converted_submission__school_id=F("school_id"),
             then=Value(APPLICATION_SUBMITTED)),
        default=F("status"),
        output_field=CharField(),
    ))


def lead_status_field(queryset):
    return "admissions_status" if "admissions_status" in queryset.query.annotations else "status"


def displayed_lead_status(lead):
    return getattr(lead, "admissions_status", lead.status)
