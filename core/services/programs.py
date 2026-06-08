"""
DB-driven program + session management service.

Functions here are called from:
  - build_yaml_sections (inject options when school.program_field_key is set)
  - inject_db_program_options (public form rendering)
  - views_public.apply_view (auto-enrollment on public submission)
  - views_school_programs (admin CRUD)
  - seed_school_programs_from_yaml management command
"""
from __future__ import annotations

import copy
import re

from django.db import transaction


# ---------------------------------------------------------------------------
# Code auto-generation helpers
# ---------------------------------------------------------------------------

def _auto_program_code(school, name: str) -> str:
    """Unique slug-like code for a program within a school."""
    from core.models import SchoolProgram
    base = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")[:40] or "program"
    code = base
    n = 2
    while SchoolProgram.objects.filter(school=school, code=code).exists():
        code = f"{base}_{n}"
        n += 1
    return code


def _auto_session_code(program, name: str) -> str:
    """Unique slug-like code for a session within a program."""
    from core.models import SchoolSession
    base = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")[:40] or "session"
    code = base
    n = 2
    while SchoolSession.objects.filter(program=program, code=code).exists():
        code = f"{base}_{n}"
        n += 1
    return code


# ---------------------------------------------------------------------------
# Public form option builders
# ---------------------------------------------------------------------------

def get_program_options(school, form_key: str = "default") -> list[dict]:
    """
    Return active SchoolProgram records as a flat options list.

    Used for lead capture dropdowns and "are any programs available" existence
    checks.  Values are bare program codes (no namespace prefix) for backwards
    compatibility with the lead capture form.

    For enrollment form rendering use inject_db_program_options() instead,
    which produces namespaced values and optgroup structure when sessions exist.
    """
    from core.models import SchoolProgram
    qs = SchoolProgram.objects.filter(school=school, is_active=True, is_deleted=False).order_by("display_order", "name")
    result = []
    for p in qs:
        if p.form_keys and form_key not in p.form_keys:
            continue
        result.append({"value": p.code, "label": p.name})
    return result


def has_enrollment_options(school, form_key: str = "default") -> bool:
    """
    Return True if there is at least one selectable enrollment option.

    Rule: once a program has any non-deleted sessions, it *only* contributes
    options via its active sessions.  A program with sessions but all inactive
    contributes nothing — the bare program is never shown as a fallback.
    """
    from core.models import SchoolProgram
    programs = SchoolProgram.objects.filter(
        school=school, is_active=True, is_deleted=False
    ).order_by("display_order", "name")
    for p in programs:
        if p.form_keys and form_key not in p.form_keys:
            continue
        has_any_sessions = p.sessions.filter(is_deleted=False).exists()
        if has_any_sessions:
            if p.has_active_sessions():
                return True
            # else: has sessions but all inactive → contributes nothing
        else:
            # No sessions → bare program is available
            return True
    return False


def _get_enrollment_option_groups(school, form_key: str = "default") -> list[dict]:
    """
    Build option groups for the enrollment dropdown.

    Return value is a list of group dicts:
      {"label": "Ballet", "options": [{"value": "session:7", "label": "..."}, ...]}

    Programs without sessions appear in a sentinel group with label="" (ungrouped).
    Programs with active sessions appear as an optgroup; bare program option is
    suppressed for those programs.
    """
    from core.models import SchoolProgram, SchoolSession

    programs = SchoolProgram.objects.filter(
        school=school, is_active=True, is_deleted=False,
    ).order_by("display_order", "name").prefetch_related("sessions")

    grouped: list[dict] = []   # programs with sessions → optgroups
    ungrouped: list[dict] = [] # programs without sessions → flat options

    for p in programs:
        if p.form_keys and form_key not in p.form_keys:
            continue

        all_sessions = [s for s in p.sessions.all() if not s.is_deleted]
        active_sessions = [s for s in all_sessions if s.is_active]
        active_sessions.sort(key=lambda s: (s.display_order, s.name))

        has_any_sessions = bool(all_sessions)

        if has_any_sessions:
            # Program uses sessions — only show active ones; never fall back to bare program.
            if active_sessions:
                options = [
                    {"value": f"session:{s.pk}", "label": f"{p.name} — {s.name}"}
                    for s in active_sessions
                ]
                grouped.append({"label": p.name, "options": options})
            # else: all sessions inactive → contributes nothing
        else:
            ungrouped.append({"value": f"program:{p.code}", "label": p.name})

    # Ungrouped items go into a sentinel group at the end (no optgroup wrapping).
    result = grouped[:]
    if ungrouped:
        result.append({"label": "", "options": ungrouped})
    return result


def inject_db_program_options(form_cfg: dict, school, form_key: str = "default") -> dict:
    """
    Return a deep-copy of form_cfg with DB program options injected for the
    field matching school.program_field_key.

    When any program for this school has active sessions, the field gets
    option_groups (optgroup rendering) instead of a flat options list.

    If no enrollment options exist, the field gets no_programs_warning=True.
    """
    field_key = getattr(school, "program_field_key", "") or ""
    if not form_cfg or not field_key:
        return form_cfg

    form_cfg = copy.deepcopy(form_cfg)
    has_options = has_enrollment_options(school, form_key=form_key)

    for section in form_cfg.get("sections", []):
        for field in section.get("fields", []):
            if field.get("key") != field_key:
                continue
            if not has_options:
                field["options"] = []
                field["no_programs_warning"] = True
            else:
                groups = _get_enrollment_option_groups(school, form_key=form_key)
                # Determine whether optgroups are needed (any named group).
                has_named_group = any(g["label"] for g in groups)
                if has_named_group:
                    field["option_groups"] = groups
                    field.pop("options", None)
                else:
                    # All ungrouped — flatten into a simple options list.
                    flat = []
                    for g in groups:
                        flat.extend(g["options"])
                    field["options"] = flat
                    field.pop("option_groups", None)
    return form_cfg


# ---------------------------------------------------------------------------
# Submission resolver
# ---------------------------------------------------------------------------

def resolve_submission_program_and_session(school, data: dict, strict: bool = True):
    """
    Parse Submission.data[program_field_key] and return (program, session).

    Handles three value formats:
      "session:<pk>"      — session-namespaced (new; sets both program and session)
      "program:<code>"    — program-namespaced (new; session=None)
      "<bare code>"       — legacy (pre-sessions; session=None)

    strict=True (default, used for public form submissions):
      - Rejects inactive or deleted sessions and programs.
      - A crafted POST with an inactive option returns (None, None).

    strict=False (admin/historical context):
      - Resolves inactive/deleted records for display purposes.

    Returns (SchoolProgram | None, SchoolSession | None).
    """
    from core.models import SchoolProgram, SchoolSession

    key = getattr(school, "program_field_key", "") or ""
    if not key:
        return None, None

    raw = (data or {}).get(key, "")
    if not raw:
        return None, None

    if raw.startswith("session:"):
        try:
            session_pk = int(raw.split(":", 1)[1])
        except (ValueError, IndexError):
            return None, None
        filters = {"pk": session_pk, "program__school": school, "is_deleted": False}
        if strict:
            filters["is_active"] = True
        try:
            session = SchoolSession.objects.select_related("program").get(**filters)
            return session.program, session
        except SchoolSession.DoesNotExist:
            return None, None

    if raw.startswith("program:"):
        code = raw.split(":", 1)[1]
    else:
        code = raw  # legacy bare code

    filters = {"school": school, "code": code}
    if strict:
        filters["is_active"] = True
        filters["is_deleted"] = False
    try:
        program = SchoolProgram.objects.get(**filters)
        return program, None
    except SchoolProgram.DoesNotExist:
        return None, None


def resolve_submission_program(school, data: dict):
    """Backwards-compat wrapper — returns only the program."""
    program, _ = resolve_submission_program_and_session(school, data)
    return program


# ---------------------------------------------------------------------------
# Auto-enrollment
# ---------------------------------------------------------------------------

def apply_auto_enrollment(school, submission, program, session=None) -> None:
    """
    Concurrency-safe auto-enrollment logic.

    When a session is provided, session-level flags and capacity take precedence
    over the program-level values.

    Behavior matrix:
      auto_enroll=True,  slots available               → STATUS_ENROLLED
      auto_enroll=True,  full, waitlist_enabled=True   → STATUS_WAITLISTED
      auto_enroll=True,  full, waitlist_enabled=False  → STATUS_NEW (no audit)
      auto_enroll=False, any                           → no-op
    """
    from core.views_school_common import STATUS_ENROLLED, STATUS_NEW, STATUS_WAITLISTED

    if session is not None:
        auto_enroll = session.auto_enroll
    else:
        auto_enroll = program.auto_enroll if program else False

    if not auto_enroll:
        return

    with transaction.atomic():
        if session is not None:
            from core.models import SchoolSession
            locked_session = SchoolSession.objects.select_for_update().get(pk=session.pk)
            capacity = locked_session.capacity
            waitlist_enabled = locked_session.waitlist_enabled
            enrolled_count_before = locked_session.submissions.filter(status=STATUS_ENROLLED).count()
            label_code = locked_session.code or str(locked_session.pk)
            label_name = locked_session.name
            from core.models import SchoolProgram
            locked_program = SchoolProgram.objects.select_for_update().get(pk=program.pk)
        else:
            from core.models import SchoolProgram
            locked_program = SchoolProgram.objects.select_for_update().get(pk=program.pk)
            locked_session = None
            capacity = locked_program.capacity
            waitlist_enabled = locked_program.waitlist_enabled
            enrolled_count_before = locked_program.submissions.filter(status=STATUS_ENROLLED).count()
            label_code = locked_program.code
            label_name = locked_program.name

        slots_available = capacity is None or enrolled_count_before < capacity

        if slots_available:
            new_status = STATUS_ENROLLED
            audit_name = "auto_enrolled"
        elif waitlist_enabled:
            new_status = STATUS_WAITLISTED
            audit_name = "auto_waitlisted"
        else:
            return

        old_status = submission.status
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
                "program_code": locked_program.code,
                "program_name": locked_program.name,
                "session_code": label_code if locked_session else None,
                "session_name": label_name if locked_session else None,
                "enrolled_count_before": enrolled_count_before,
                "enrolled_count_after": enrolled_count_after,
                "capacity": capacity,
                "waitlist_enabled": waitlist_enabled,
                "old_status": old_status,
                "new_status": new_status,
            },
        )


# ---------------------------------------------------------------------------
# Admin summary helpers
# ---------------------------------------------------------------------------

def get_programs_summary(school) -> dict:
    """
    Returns per-program summary for admin program list.
    {code: {name, capacity, enrolled_count, waitlisted_count, is_active,
            display_order, has_sessions, sessions: [...]}}
    """
    from core.models import SchoolProgram, SchoolSession
    from core.views_school_common import STATUS_ENROLLED, STATUS_WAITLISTED

    programs = SchoolProgram.objects.filter(school=school, is_deleted=False).order_by("display_order", "name")
    result = {}
    for p in programs:
        # Total across all submissions (including session submissions).
        enrolled = p.submissions.filter(status=STATUS_ENROLLED).count()
        waitlisted = p.submissions.filter(status=STATUS_WAITLISTED).count()
        # Program-level only: submissions with no session (pre-sessions or no-session schools).
        pl_enrolled = p.submissions.filter(status=STATUS_ENROLLED, session__isnull=True).count()
        pl_waitlisted = p.submissions.filter(status=STATUS_WAITLISTED, session__isnull=True).count()

        sessions_qs = SchoolSession.objects.filter(program=p, is_deleted=False).order_by("display_order", "name")
        session_rows = []
        for s in sessions_qs:
            s_enrolled = s.submissions.filter(status=STATUS_ENROLLED).count()
            s_waitlisted = s.submissions.filter(status=STATUS_WAITLISTED).count()
            session_rows.append({
                "id": s.pk,
                "name": s.name,
                "code": s.code,
                "capacity": s.capacity,
                "enrolled_count": s_enrolled,
                "waitlisted_count": s_waitlisted,
                "is_active": s.is_active,
                "auto_enroll": s.auto_enroll,
                "waitlist_enabled": s.waitlist_enabled,
                "has_submissions": s_enrolled > 0 or s_waitlisted > 0 or s.submissions.exists(),
                "start_date": s.start_date,
                "end_date": s.end_date,
            })

        result[p.code] = {
            "id": p.pk,
            "name": p.name,
            "code": p.code,
            "capacity": p.capacity,
            "enrolled_count": enrolled,
            "waitlisted_count": waitlisted,
            # Submissions with no session FK (program-level or pre-sessions).
            "program_level_enrolled": pl_enrolled,
            "program_level_waitlisted": pl_waitlisted,
            "is_active": p.is_active,
            "display_order": p.display_order,
            "auto_enroll": p.auto_enroll,
            "waitlist_enabled": p.waitlist_enabled,
            "has_submissions": enrolled > 0 or waitlisted > 0 or p.submissions.exists(),
            "has_sessions": len(session_rows) > 0,
            "sessions": session_rows,
        }
    return result
