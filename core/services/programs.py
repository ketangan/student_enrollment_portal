"""
DB-driven program management service.

Functions here are called from:
  - build_yaml_sections (inject options when school.program_field_key is set)
  - inject_db_program_options (public form rendering)
  - views_public.apply_view (auto-enrollment on public submission)
  - views_school_programs (admin CRUD)
  - seed_school_programs_from_yaml management command
"""
from __future__ import annotations

import copy

from django.db import transaction


def get_program_options(school, form_key: str = "default") -> list[dict]:
    """
    Return active SchoolProgram records as options list for form rendering.

    Respects form_keys: a program with a non-empty form_keys list is only
    returned when form_key appears in that list. An empty form_keys list means
    "available on all forms."
    """
    from core.models import SchoolProgram
    qs = SchoolProgram.objects.filter(school=school, is_active=True, is_deleted=False).order_by("display_order", "name")
    result = []
    for p in qs:
        if p.form_keys and form_key not in p.form_keys:
            continue
        result.append({"value": p.code, "label": p.name})
    return result


def inject_db_program_options(form_cfg: dict, school, form_key: str = "default") -> dict:
    """
    Return a deep-copy of form_cfg with DB program options injected for the
    field matching school.program_field_key.

    If no active programs exist for this school/form_key, the field gets
    options=[] and no_programs_warning=True so the template can block submission.
    """
    field_key = getattr(school, "program_field_key", "") or ""
    if not form_cfg or not field_key:
        return form_cfg

    options = get_program_options(school, form_key=form_key)
    form_cfg = copy.deepcopy(form_cfg)
    for section in form_cfg.get("sections", []):
        for field in section.get("fields", []):
            if field.get("key") == field_key:
                if options:
                    field["options"] = options
                else:
                    field["options"] = []
                    field["no_programs_warning"] = True
    return form_cfg


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
        return SchoolProgram.objects.get(school=school, code=code, is_deleted=False)
    except SchoolProgram.DoesNotExist:
        return None


def apply_auto_enrollment(school, submission, program) -> None:
    """
    Concurrency-safe auto-enrollment logic.

    Behavior matrix:
      auto_enroll=True,  slots available               → STATUS_ENROLLED
      auto_enroll=True,  full, waitlist_enabled=True   → STATUS_WAITLISTED
      auto_enroll=True,  full, waitlist_enabled=False  → STATUS_NEW (no audit)
      auto_enroll=False, any                           → no-op (caller must not call this)

    Uses select_for_update() on SchoolProgram to prevent concurrent over-enrollment.
    Audit-logs auto_enrolled and auto_waitlisted as system events (actor=None).
    Does NOT log when status stays New (auto_enroll_skipped).
    """
    from core.views_school_common import STATUS_ENROLLED, STATUS_NEW, STATUS_WAITLISTED

    if not program or not program.auto_enroll:
        return

    with transaction.atomic():
        from core.models import SchoolProgram
        locked = SchoolProgram.objects.select_for_update().get(pk=program.pk)

        old_status = submission.status
        enrolled_count_before = locked.submissions.filter(status=STATUS_ENROLLED).count()
        slots_available = locked.capacity is None or enrolled_count_before < locked.capacity

        if slots_available:
            new_status = STATUS_ENROLLED
            audit_name = "auto_enrolled"
        elif locked.waitlist_enabled:
            new_status = STATUS_WAITLISTED
            audit_name = "auto_waitlisted"
        else:
            # Status stays New — no audit event, no status change needed
            return

        submission.status = new_status
        submission.save(update_fields=["status"])

        enrolled_count_after = enrolled_count_before + (1 if new_status == STATUS_ENROLLED else 0)

        from core.admin.audit import log_admin_audit
        log_admin_audit(
            request=None,
            action="action",
            obj=submission,
            changes={},
            extra={
                "name": audit_name,
                "submission_id": submission.pk,
                "public_id": getattr(submission, "public_id", None),
                "school_slug": getattr(school, "slug", None),
                "program_code": locked.code,
                "program_name": locked.name,
                "enrolled_count_before": enrolled_count_before,
                "enrolled_count_after": enrolled_count_after,
                "capacity": locked.capacity,
                "waitlist_enabled": locked.waitlist_enabled,
                "old_status": old_status,
                "new_status": new_status,
            },
        )


def get_programs_summary(school) -> dict:
    """
    Returns per-program summary for admin program list.
    {code: {name, capacity, enrolled_count, waitlisted_count, is_active, display_order}}
    """
    from core.models import SchoolProgram
    from core.views_school_common import STATUS_ENROLLED, STATUS_WAITLISTED
    programs = SchoolProgram.objects.filter(school=school, is_deleted=False).order_by("display_order", "name")
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
