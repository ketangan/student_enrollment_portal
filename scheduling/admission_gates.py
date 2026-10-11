"""Narrow entry-point adapter. Missing/disabled policy preserves legacy enrollment."""
from functools import wraps

from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import add_never_cache_headers

from core.models import DraftSubmission
from . import admissions
from .models import AdmissionPolicy, SchedulingProfile, VisitPilotSettings


def application_gate(view):
    @wraps(view)
    def guarded(request, school_slug, *args, **kwargs):
        policy = AdmissionPolicy.objects.select_related("profile__school").filter(profile__school__slug=school_slug, enabled=True).first()
        if policy is None:
            return view(request, school_slug, *args, **kwargs)
        # This local operational pilot is deliberately free. Never enter legacy
        # fee collection or its fail-open fallback under the new demo settings.
        if VisitPilotSettings.objects.filter(profile=policy.profile).exists():
            from core.services.config_loader import load_school_config, apply_overrides
            config = apply_overrides(load_school_config(school_slug), policy.profile.school.config_overrides)
            raw = getattr(config, "raw", {}) or {}
            fee = raw.get("application_fee") or {}
            form_fees = [value.get("application_fee") or {} for value in (raw.get("forms") or {}).values() if isinstance(value, dict)]
            if view.__name__ in {"apply_payment_view", "apply_payment_confirm_view", "apply_payment_bypass_view"} or any(
                    value.get("enabled") for value in [fee, *form_fees]):
                response = render(request, "scheduling/admissions/blocked.html", {"business": policy.profile.school,
                    "reason": "Paid applications are not enabled for this visit demo. Please contact the school."}, status=403)
                add_never_cache_headers(response)
                return response
        # Order matches visit writers. Also serializes release/revocation against final submit.
        with transaction.atomic():
            SchedulingProfile.objects.select_for_update().get(pk=policy.profile_id)
            policy = AdmissionPolicy.objects.select_related("profile__school").get(pk=policy.pk)
            token = kwargs.get("token") or kwargs.get("draft_token") or request.POST.get("_draft_token")
            draft = DraftSubmission.objects.filter(school_id=policy.profile.school_id)
            draft = draft.filter(token=token).first() if token else draft.filter(pk=request.session.get(f"apply_draft_id:{school_slug}")).first()
            case = None
            reason = "Please use the application link provided after your tour."
            if draft and draft.lead_id:
                case = admissions.case_for_lead(policy, draft.lead)
                invalid_link = draft.is_expired() or (case.draft_id and case.draft_id != draft.pk)
                if case.submission_id and not invalid_link:
                    response = redirect("family_status", school_slug=school_slug, token=case.submission.status_token)
                    add_never_cache_headers(response)
                    return response
                reason = admissions.application_reason(case)
                if invalid_link or draft.is_submitted():
                    reason = "This application link is no longer available. Please contact the school."
                if not reason:
                    if case.pk is None:
                        case.save()
                    case.draft = draft
                    case.save(update_fields=["draft"])
                    request.booking_admission = case
            if reason:
                response = render(request, "scheduling/admissions/blocked.html", {"business": policy.profile.school, "reason": reason}, status=403)
            else:
                response = view(request, school_slug, *args, **kwargs)
                case.refresh_from_db()
                if case.submission_id and response.status_code == 302:
                    response = redirect("family_status", school_slug=school_slug, token=case.submission.status_token)
            add_never_cache_headers(response)
            return response
    return guarded


def lead_application_gate(view):
    @wraps(view)
    def guarded(request, school_slug, lead_id, *args, **kwargs):
        from core.views_school_common import _get_accessible_school_for_admin
        from core.models import Lead
        policy = AdmissionPolicy.objects.filter(profile__school__slug=school_slug, enabled=True).first()
        if policy is None:
            return view(request, school_slug, lead_id, *args, **kwargs)
        school = _get_accessible_school_for_admin(request, school_slug)
        with transaction.atomic():
            SchedulingProfile.objects.select_for_update().get(pk=policy.profile_id)
            lead = get_object_or_404(Lead, pk=lead_id, school=school)
            context = admissions.lead_context(lead)
            if context and not context["allowed"]:
                from django.contrib import messages
                messages.error(request, context["message"])
                return redirect("school_lead_detail", school_slug=school_slug, lead_id=lead_id)
            return view(request, school_slug, lead_id, *args, **kwargs)
    return guarded
