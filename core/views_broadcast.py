import json
import logging

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import (
    BroadcastMessage,
    BroadcastRecipient,
    Lead,
    LEAD_STATUS_CHOICES,
    Submission,
)
from core.services.notifications import send_admin_message
from core.services.school_permissions import require_school_role
from core.views_school_common import _get_accessible_school_for_admin, _school_admin_base_context

logger = logging.getLogger(__name__)

# Email keys to try, in priority order, when extracting contact email from a Submission.
_SUB_EMAIL_KEYS = ("contact_email", "guardian_email", "parent_email", "email", "applicant_email")
_SUB_NAME_KEYS = ("parent_name", "guardian_name", "applicant_name", "name")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lead_programs_for_school(school):
    """Distinct interested_in_value values that have at least one lead."""
    return list(
        Lead.objects.filter(school=school)
        .exclude(interested_in_value="")
        .values_list("interested_in_value", flat=True)
        .distinct()
        .order_by("interested_in_value")
    )


def _submission_programs_for_school(school):
    """Distinct program names from SchoolProgram or Submission.program FK."""
    from core.models import SchoolProgram
    pqs = SchoolProgram.objects.filter(school=school).order_by("name").values_list("name", flat=True)
    if pqs.exists():
        return list(pqs)
    # Fallback: collect from FK on submissions
    return list(
        Submission.objects.filter(school=school, program__isnull=False)
        .values_list("program__name", flat=True)
        .distinct()
        .order_by("program__name")
    )


def _build_audience(school, include_leads, include_submissions, leads_filter, submissions_filter):
    """
    Return (recipients, skipped) where recipients is a list of dicts:
      {email, name, source, source_id}
    Deduplicates by lower-cased email; first source seen wins.
    skipped counts empty/invalid emails.
    """
    seen = {}   # normalized email → dict
    skipped = 0

    if include_leads:
        qs = Lead.objects.filter(school=school)
        statuses = leads_filter.get("statuses") or []
        programs = leads_filter.get("programs") or []
        if statuses:
            qs = qs.filter(status__in=statuses)
        if programs:
            qs = qs.filter(interested_in_value__in=programs)
        for lead in qs.values("id", "name", "email"):
            email = (lead["email"] or "").strip()
            if not email:
                skipped += 1
                continue
            key = email.lower()
            if key not in seen:
                seen[key] = {
                    "email": email,
                    "name": lead["name"] or "",
                    "source": BroadcastRecipient.SOURCE_LEAD,
                    "source_id": lead["id"],
                }

    if include_submissions:
        qs = Submission.objects.filter(school=school).prefetch_related()
        programs = submissions_filter.get("programs") or []
        if programs:
            qs = qs.filter(program__name__in=programs)
        for sub in qs:
            email = None
            for k in _SUB_EMAIL_KEYS:
                v = sub.data.get(k, "")
                if v and isinstance(v, str):
                    email = v.strip()
                    break
            if not email:
                skipped += 1
                continue
            key = email.lower()
            if key not in seen:
                name = ""
                for k in _SUB_NAME_KEYS:
                    v = sub.data.get(k, "")
                    if v and isinstance(v, str):
                        name = v.strip()
                        break
                seen[key] = {
                    "email": email,
                    "name": name,
                    "source": BroadcastRecipient.SOURCE_SUBMISSION,
                    "source_id": sub.id,
                }

    return list(seen.values()), skipped


def _gate(request, school_slug):
    """Check access: feature flag + editor role. Returns school or raises."""
    school = _get_accessible_school_for_admin(request, school_slug)
    if not school.features.broadcast_enabled:
        raise Http404("Broadcast not available for this school")
    require_school_role(request, school, "editor")
    return school


# ── Views ─────────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(["GET", "POST"])
def school_broadcast_view(request, school_slug):
    """Compose form + sent history."""
    school = _gate(request, school_slug)

    lead_statuses = LEAD_STATUS_CHOICES
    lead_programs = _lead_programs_for_school(school)
    sub_programs = _submission_programs_for_school(school)

    if request.method == "POST":
        # Stash form data in session and redirect to preview
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        cc_email = request.POST.get("cc_email", "").strip()
        include_leads = "include_leads" in request.POST
        include_submissions = "include_submissions" in request.POST
        leads_statuses = request.POST.getlist("leads_statuses")
        leads_programs = request.POST.getlist("leads_programs")
        sub_programs_sel = request.POST.getlist("sub_programs")

        errors = []
        if not subject:
            errors.append("Subject is required.")
        if not body:
            errors.append("Message body is required.")
        if not include_leads and not include_submissions:
            errors.append("Select at least one audience source (Leads or Submissions).")

        if errors:
            ctx = _school_admin_base_context(request, school, "broadcast")
            ctx.update({
                "lead_statuses": lead_statuses,
                "lead_programs": lead_programs,
                "sub_programs": sub_programs,
                "sent_broadcasts": _sent_broadcasts(school),
                "errors": errors,
                "form": {
                    "subject": subject, "body": body, "cc_email": cc_email,
                    "include_leads": include_leads, "include_submissions": include_submissions,
                    "leads_statuses": leads_statuses, "leads_programs": leads_programs,
                    "sub_programs": sub_programs_sel,
                },
            })
            return render(request, "school_admin/broadcast.html", ctx)

        leads_filter = {"statuses": leads_statuses, "programs": leads_programs}
        submissions_filter = {"programs": sub_programs_sel}

        request.session["broadcast_draft"] = {
            "subject": subject,
            "body": body,
            "cc_email": cc_email,
            "include_leads": include_leads,
            "include_submissions": include_submissions,
            "leads_filter": leads_filter,
            "submissions_filter": submissions_filter,
        }
        return redirect("school_broadcast_preview", school_slug=school_slug)

    ctx = _school_admin_base_context(request, school, "broadcast")
    ctx.update({
        "lead_statuses": lead_statuses,
        "lead_programs": lead_programs,
        "sub_programs": sub_programs,
        "sent_broadcasts": _sent_broadcasts(school),
        "errors": [],
        "form": {},
    })
    return render(request, "school_admin/broadcast.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_broadcast_preview_view(request, school_slug):
    """Preview screen: shows recipient count + email body before sending."""
    school = _gate(request, school_slug)
    draft = request.session.get("broadcast_draft")

    if not draft:
        return redirect("school_broadcast", school_slug=school_slug)

    if request.method == "POST":
        # Actually send
        return _do_send(request, school, draft, school_slug)

    recipients, skipped = _build_audience(
        school,
        draft["include_leads"],
        draft["include_submissions"],
        draft["leads_filter"],
        draft["submissions_filter"],
    )

    ctx = _school_admin_base_context(request, school, "broadcast")
    ctx.update({
        "draft": draft,
        "recipients": recipients,
        "recipient_count": len(recipients),
        "skipped_count": skipped,
    })
    return render(request, "school_admin/broadcast_preview.html", ctx)


def _do_send(request, school, draft, school_slug):
    recipients, skipped = _build_audience(
        school,
        draft["include_leads"],
        draft["include_submissions"],
        draft["leads_filter"],
        draft["submissions_filter"],
    )

    if not recipients:
        messages.error(request, "No recipients matched. Nothing was sent.")
        return redirect("school_broadcast_preview", school_slug=school_slug)

    sent_count = 0
    failed_count = 0
    recipient_rows = []

    for r in recipients:
        ok = send_admin_message(
            to_email=r["email"],
            subject=draft["subject"],
            message=draft["body"],
            school_name=school.display_name or school.slug,
            from_email=school.smtp_from_email or None,
            school=school,
        )
        status = BroadcastRecipient.STATUS_SENT if ok else BroadcastRecipient.STATUS_FAILED
        if ok:
            sent_count += 1
        else:
            failed_count += 1
        recipient_rows.append({**r, "status": status})

    with transaction.atomic():
        bm = BroadcastMessage.objects.create(
            school=school,
            subject=draft["subject"],
            body=draft["body"],
            cc_email=draft.get("cc_email", ""),
            include_leads=draft["include_leads"],
            include_submissions=draft["include_submissions"],
            leads_filter=draft["leads_filter"],
            submissions_filter=draft["submissions_filter"],
            created_by=request.user,
            recipient_count=len(recipients),
            sent_count=sent_count,
            skipped_count=skipped,
            failed_count=failed_count,
        )
        BroadcastRecipient.objects.bulk_create([
            BroadcastRecipient(
                broadcast=bm,
                email=r["email"],
                name=r["name"],
                source=r["source"],
                source_id=r["source_id"],
                status=r["status"],
            )
            for r in recipient_rows
        ])

    del request.session["broadcast_draft"]
    messages.success(
        request,
        f"Broadcast sent to {sent_count} recipient{'s' if sent_count != 1 else ''}."
        + (f" {failed_count} failed." if failed_count else ""),
    )
    return redirect("school_broadcast", school_slug=school_slug)


def _sent_broadcasts(school):
    return (
        BroadcastMessage.objects.filter(school=school)
        .prefetch_related("recipients")
        .order_by("-sent_at")[:50]
    )
