import json
import logging

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.models import (
    BroadcastMessage,
    BroadcastRecipient,
    Lead,
    LEAD_STATUS_CHOICES,
    SchoolCustomToken,
    Submission,
)
from core.services.notifications import send_admin_message, _render_template
from core.services.school_permissions import require_school_role
from core.views_school_common import _get_accessible_school_for_admin, _school_admin_base_context
from core.views_school_email_templates import _TOKENS as _AUTO_TOKENS

logger = logging.getLogger(__name__)

# Tokens shown as inline pills (subset of _AUTO_TOKENS — the broadly applicable ones).
_PILL_TOKEN_KEYS = {"full_name", "first_name", "email", "program", "school_name"}

# Extra tokens available in broadcast but not in the shared _AUTO_TOKENS list.
_BROADCAST_EXTRA_TOKENS = [
    ("last_name",    "Last name"),
    ("program_name", "Program name (alias)"),
]


def _merge_field_context(school):
    """
    Returns dict with merge_pill_fields, merge_extra_auto, merge_custom.
    Derives from _AUTO_TOKENS (single source of truth) + SchoolCustomToken.
    """
    pill_fields = []
    extra_auto_fields = []
    for key, label in _AUTO_TOKENS:
        entry = {"key": "{{" + key + "}}", "token_key": key, "label": label, "display": "{{" + key + "}}"}
        if key in _PILL_TOKEN_KEYS:
            pill_fields.append(entry)
        else:
            extra_auto_fields.append(entry)
    # Broadcast-specific extras go in the dropdown alongside status
    for key, label in _BROADCAST_EXTRA_TOKENS:
        extra_auto_fields.append({"key": "{{" + key + "}}", "token_key": key, "label": label, "display": "{{" + key + "}}"})
    custom_fields = [
        {"key": "{{" + t.key + "}}", "token_key": t.key, "label": t.label, "display": "{{" + t.key + "}}"}
        for t in SchoolCustomToken.objects.filter(school=school).order_by("key")
    ]
    return {
        "merge_pill_fields": pill_fields,
        "merge_extra_auto": extra_auto_fields,
        "merge_custom": custom_fields,
    }

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


def _merge_data_for(name, email, program, school_name, status="", extra=None):
    """Build the merge context dict used by _render_template for one recipient."""
    parts = (name or "").split()
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) > 1 else ""
    data = {
        "first_name": first,
        "last_name": last,
        "full_name": name or "",
        "email": email,
        "program": program or "",
        "program_name": program or "",   # alias so {{program_name}} also works
        "status": status or "",
        "school_name": school_name,
    }
    if extra:
        data.update(extra)
    return data


def _build_audience(school, include_leads, include_submissions, leads_filter, submissions_filter):
    """
    Return (recipients, skipped) where recipients is a list of dicts:
      {email, name, source, source_id, merge_data}
    Deduplicates by lower-cased email; first source seen wins.
    skipped counts empty/invalid emails.
    merge_data includes auto-filled tokens + custom token values from form data.
    """
    seen = {}   # normalized email → dict
    skipped = 0
    school_name = school.display_name or school.slug
    custom_token_keys = list(
        SchoolCustomToken.objects.filter(school=school).values_list("key", flat=True)
    )
    # code → display name lookup so lead.interested_in_value renders as a human label.
    from core.models import SchoolProgram
    program_name_map = dict(
        SchoolProgram.objects.filter(school=school).values_list("code", "name")
    )

    if include_leads:
        qs = Lead.objects.filter(school=school)
        statuses = leads_filter.get("statuses") or []
        programs = leads_filter.get("programs") or []
        if statuses:
            qs = qs.filter(status__in=statuses)
        if programs:
            qs = qs.filter(interested_in_value__in=programs)
        for lead in qs.values("id", "name", "email", "interested_in_value", "interested_in_label", "status"):
            email = (lead["email"] or "").strip()
            if not email:
                skipped += 1
                continue
            key = email.lower()
            if key not in seen:
                name = lead["name"] or ""
                raw_program = lead["interested_in_value"] or ""
                # Prefer the stored human label; fall back to SchoolProgram lookup for
                # legacy leads created before interested_in_label was populated.
                program_display = (
                    lead["interested_in_label"]
                    or program_name_map.get(raw_program, raw_program)
                )
                seen[key] = {
                    "email": email,
                    "name": name,
                    "source": BroadcastRecipient.SOURCE_LEAD,
                    "source_id": lead["id"],
                    "merge_data": _merge_data_for(
                        name, email, program_display, school_name,
                        status=lead["status"] or "",
                    ),
                }

    if include_submissions:
        qs = Submission.objects.filter(school=school).select_related("program")
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
                program_name = sub.program.name if sub.program else ""
                # Pull custom token values from form data where available.
                custom_vals = {
                    tk: str(sub.data[tk])
                    for tk in custom_token_keys
                    if tk in sub.data and sub.data[tk]
                }
                seen[key] = {
                    "email": email,
                    "name": name,
                    "source": BroadcastRecipient.SOURCE_SUBMISSION,
                    "source_id": sub.id,
                    "merge_data": _merge_data_for(
                        name, email, program_name, school_name,
                        status=sub.status or "",
                        extra=custom_vals,
                    ),
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
        bcc_email = request.POST.get("bcc_email", "").strip()
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
            ctx.update(_merge_field_context(school))
            ctx.update({
                "lead_statuses": lead_statuses,
                "lead_programs": lead_programs,
                "sub_programs": sub_programs,
                "sent_broadcasts": _sent_broadcasts(school),
                "active_tab": "compose",
                "errors": errors,
                "form": {
                    "subject": subject, "body": body, "cc_email": cc_email, "bcc_email": bcc_email,
                    "include_leads": include_leads, "include_submissions": include_submissions,
                    "leads_statuses": leads_statuses, "leads_programs": leads_programs,
                    "sub_programs": sub_programs_sel,
                    "leads_statuses_json": json.dumps(leads_statuses),
                    "leads_programs_json": json.dumps(leads_programs),
                    "sub_programs_json": json.dumps(sub_programs_sel),
                },
            })
            return render(request, "school_admin/broadcast.html", ctx)

        leads_filter = {"statuses": leads_statuses, "programs": leads_programs}
        submissions_filter = {"programs": sub_programs_sel}

        request.session["broadcast_draft"] = {
            "subject": subject,
            "body": body,
            "cc_email": cc_email,
            "bcc_email": bcc_email,
            "include_leads": include_leads,
            "include_submissions": include_submissions,
            "leads_filter": leads_filter,
            "submissions_filter": submissions_filter,
        }
        return redirect("school_broadcast_preview", school_slug=school_slug)

    active_tab = request.GET.get("tab", "compose")
    if active_tab not in ("compose", "sent"):
        active_tab = "compose"

    # Restore in-progress draft so "Edit message ← " repopulates the form.
    draft = request.session.get("broadcast_draft") or {}
    lf = draft.get("leads_filter") or {}
    sf = draft.get("submissions_filter") or {}
    ls = lf.get("statuses") or []
    lp = lf.get("programs") or []
    sp = sf.get("programs") or []
    form_ctx = {
        "subject": draft.get("subject", ""),
        "body": draft.get("body", ""),
        "cc_email": draft.get("cc_email", ""),
        "bcc_email": draft.get("bcc_email", ""),
        "include_leads": draft.get("include_leads", False),
        "include_submissions": draft.get("include_submissions", False),
        "leads_statuses": ls,
        "leads_programs": lp,
        "sub_programs": sp,
        "leads_statuses_json": json.dumps(ls),
        "leads_programs_json": json.dumps(lp),
        "sub_programs_json": json.dumps(sp),
    }

    ctx = _school_admin_base_context(request, school, "broadcast")
    ctx.update(_merge_field_context(school))
    ctx.update({
        "lead_statuses": lead_statuses,
        "lead_programs": lead_programs,
        "sub_programs": sub_programs,
        "sent_broadcasts": _sent_broadcasts(school),
        "active_tab": active_tab,
        "errors": [],
        "form": form_ctx,
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

    # Render preview using first recipient's merge data so tokens are substituted.
    preview_merge = recipients[0]["merge_data"] if recipients else {}
    preview_subject = _render_template(draft["subject"], preview_merge)
    preview_body = _render_template(draft["body"], preview_merge)

    # Pass merge data for up to 50 recipients so JS can re-render on dropdown change.
    preview_recipients_json = json.dumps([
        {
            "name": r["name"],
            "email": r["email"],
            "merge_data": r.get("merge_data") or {},
        }
        for r in recipients[:50]
    ])

    ctx = _school_admin_base_context(request, school, "broadcast")
    ctx.update({
        "draft": draft,
        "recipients": recipients,
        "recipient_count": len(recipients),
        "skipped_count": skipped,
        "preview_subject": preview_subject,
        "preview_body": preview_body,
        "preview_recipient_name": recipients[0]["name"] if recipients else "",
        "preview_recipient_email": recipients[0]["email"] if recipients else "",
        "preview_recipients_json": preview_recipients_json,
        "draft_subject_raw_json": json.dumps(draft["subject"]),
        "draft_body_raw_json": json.dumps(draft["body"]),
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

    school_name = school.display_name or school.slug
    for r in recipients:
        merge = r.get("merge_data") or {}
        personalised_subject = _render_template(draft["subject"], merge)
        personalised_body = _render_template(draft["body"], merge)
        ok = send_admin_message(
            to_email=r["email"],
            subject=personalised_subject,
            message=personalised_body,
            school_name=school_name,
            from_email=school.smtp_from_email or None,
            cc_email=draft.get("cc_email") or None,
            bcc_email=draft.get("bcc_email") or None,
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
            bcc_email=draft.get("bcc_email", ""),
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
                merge_data=r.get("merge_data") or {},
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
    return redirect(reverse("school_broadcast", kwargs={"school_slug": school_slug}) + "?tab=sent")


def _sent_broadcasts(school):
    return (
        BroadcastMessage.objects.filter(school=school)
        .prefetch_related("recipients")
        .order_by("-sent_at")[:50]
    )


@login_required
@require_http_methods(["POST"])
def school_broadcast_audience_api(request, school_slug):
    """Return JSON recipient list for the current filter state (used by live preview in compose)."""
    school = _gate(request, school_slug)

    include_leads = request.POST.get("include_leads") == "1"
    include_submissions = request.POST.get("include_submissions") == "1"
    leads_statuses = request.POST.getlist("leads_statuses")
    leads_programs = request.POST.getlist("leads_programs")
    sub_programs = request.POST.getlist("sub_programs")

    if not include_leads and not include_submissions:
        return JsonResponse({"count": 0, "skipped": 0, "recipients": []})

    leads_filter = {"statuses": leads_statuses, "programs": leads_programs}
    submissions_filter = {"programs": sub_programs}

    recipients, skipped = _build_audience(
        school, include_leads, include_submissions, leads_filter, submissions_filter
    )

    return JsonResponse({
        "count": len(recipients),
        "skipped": skipped,
        "recipients": [
            {"name": r["name"], "email": r["email"], "source": r["source"]}
            for r in recipients[:50]
        ],
    })
