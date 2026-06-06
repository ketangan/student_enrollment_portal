"""
DB-driven program management service.

Functions here are called from:
  - build_yaml_sections (inject options when school.program_field_key is set)
  - views_public.apply_view (auto-enrollment on public submission)
  - views_school_programs (admin CRUD)
  - seed_school_programs_from_yaml management command
"""
from __future__ import annotations

from django.db import transaction

from core.views_school_common import STATUS_ENROLLED, STATUS_NEW, STATUS_WAITLISTED


def get_program_options(school) -> list[dict]:
    """Return active SchoolProgram records as options list for form rendering."""
    from core.models import SchoolProgram
    programs = SchoolProgram.objects.filter(school=school, is_active=True).order_by("display_order", "name")
    return [{"value": p.code, "label": p.name} for p in programs]


def resolve_submission_program(school, data: dict):
    """
    Match Submission.data[program_field_key] to a SchoolProgram record.
    Returns SchoolProgram or None.
    """
    from core.models import SchoolProgram
    key = getattr(school, "program_field_key", "") or ""
    if not key:
        return None
    code = (data or {}).get(key, "")
    if not code:
        return None
    try:
        return SchoolProgram.objects.get(school=school, code=code)
    except SchoolProgram.DoesNotExist:
        return None


def apply_auto_enrollment(school, submission, program) -> None:
    """
    Concurrency-safe auto-enrollment logic.

    Behavior matrix:
      auto_enroll=True,  slots available                  → STATUS_ENROLLED
      auto_enroll=True,  full, waitlist_enabled=True      → STATUS_WAITLISTED
      auto_enroll=True,  full, waitlist_enabled=False     → STATUS_NEW
      auto_enroll=False, any                              → STATUS_NEW (no-op, caller should not call this)

    Uses select_for_update() on the SchoolProgram row to prevent concurrent over-enrollment.
    """
    if not program or not program.auto_enroll:
        return

    with transaction.atomic():
        # Lock the program row to serialize concurrent enrollments
        from core.models import SchoolProgram
        locked = SchoolProgram.objects.select_for_update().get(pk=program.pk)

        enrolled_count = locked.submissions.filter(status=STATUS_ENROLLED).count()
        slots_available = locked.capacity is None or enrolled_count < locked.capacity

        if slots_available:
            new_status = STATUS_ENROLLED
        elif locked.waitlist_enabled:
            new_status = STATUS_WAITLISTED
        else:
            new_status = STATUS_NEW

        submission.status = new_status
        submission.save(update_fields=["status"])


def get_programs_summary(school) -> dict:
    """
    Returns per-program summary for admin program list.
    {code: {name, capacity, enrolled_count, waitlisted_count, is_active, display_order}}
    """
    from core.models import SchoolProgram
    from core.views_school_common import STATUS_WAITLISTED, STATUS_ENROLLED
    programs = SchoolProgram.objects.filter(school=school).order_by("display_order", "name")
    result = {}
    for p in programs:
        enrolled = p.submissions.filter(status=STATUS_ENROLLED).count()
        waitlisted = p.submissions.filter(status=STATUS_WAITLISTED).count()
        result[p.code] = {
            "id": p.pk,
            "name": p.name,
            "code": p.code,
            "capacity": p.capacity,
            "enrolled_count": enrolled,
            "waitlisted_count": waitlisted,
            "is_active": p.is_active,
            "display_order": p.display_order,
            "auto_enroll": p.auto_enroll,
            "waitlist_enabled": p.waitlist_enabled,
            "has_submissions": enrolled > 0 or waitlisted > 0 or p.submissions.exists(),
        }
    return result
