"""Enrollment policy and identity linking. Writers share the scheduling tenant lock."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from core.models import DraftSubmission, Lead, Submission
from core.services.school_permissions import get_school_membership
from .models import AdmissionCase, AdmissionPolicy, SchedulingProfile, VisitBooking
from .setup import _audit


def policy_for(school_id):
    return AdmissionPolicy.objects.select_related("profile__school").filter(profile__school_id=school_id, enabled=True).first()


def cases(policy):
    return AdmissionCase.objects.filter(policy=policy).select_related("policy__profile__school", "lead", "tour", "draft", "submission")


def running(policy):
    return (policy.enabled and policy.profile.enabled and "visits" in policy.profile.capabilities
        and policy.profile.school.is_active and not policy.profile.school.is_trial_expired
        and policy.profile.school.features.family_portal_enabled)


def application_reason(case):
    if not running(case.policy):
        return "Applications are paused. Please contact the school."
    if case.submission_id or case.lead.converted_submission_id:
        return "An application has already been submitted."
    if case.lead.status == "lost":
        return "Please contact the school about this application."
    if case.policy.require_tour and (not case.tour_id or case.tour.state != "attended"):
        return "Complete your tour before starting the application."
    if case.policy.application_release == "staff" and not case.application_released_at:
        return "The school will release your application after reviewing your visit."
    return ""


def playdate_reason(case):
    if not running(case.policy):
        return "Playdate bookings are paused. Please contact the school."
    if not case.submission_id:
        return "Submit your application before booking a playdate."
    if case.playdates.filter(state__in=["booked", "attended"]).exists():
        return "Your playdate is already booked or completed."
    if case.submission.status not in case.policy.eligible_statuses:
        return "The school is reviewing your application before playdate booking."
    if case.policy.playdate_release == "staff" and not case.playdate_released_at:
        return "The school will let you know when you can book a playdate."
    return ""


def case_for_lead(policy, lead, *, create=False):
    if lead.school_id != policy.profile.school_id:
        raise ValidationError("This lead belongs to another school.")
    saved = cases(policy).filter(lead=lead).first()
    if saved:
        return saved
    tour = VisitBooking.objects.filter(lead=lead, slot__program__kind="tour",
        slot__program__offering__location__profile=policy.profile).order_by("-created_at").first()
    case = AdmissionCase(policy=policy, lead=lead, tour=tour)
    if create:
        if lead.converted_submission_id:
            raise ValidationError("This existing application is not connected to the visit workflow.")
        case.save()
    return case


def lead_context(lead):
    policy = policy_for(lead.school_id)
    if not policy:
        return None
    case = case_for_lead(policy, lead)
    reason = application_reason(case)
    return {"allowed": not reason, "message": reason,
        "status_url": reverse("family_status", kwargs={"school_slug": lead.school.slug,
            "token": case.submission.status_token}) if case.submission_id else None,
        "url": reverse("scheduling:admission_case",
        kwargs={"school_slug": lead.school.slug, "lead_pk": lead.pk})}


def require_editor(user, profile, *, owner=False):
    from .visits import enrollment_access
    membership = get_school_membership(user, profile.school)
    if not enrollment_access(user, profile, owner=owner) or (not user.is_superuser
            and (not membership or not membership.has_role("owner" if owner else "editor"))):
        from django.http import Http404
        raise Http404("Enrollment access is required.")


@transaction.atomic
def prepare_lead_draft(lead):
    policy = policy_for(lead.school_id)
    if not policy:
        raise ValidationError("Admissions settings changed. Reload the lead.")
    SchedulingProfile.objects.select_for_update().get(pk=policy.profile_id)
    policy = policy_for(lead.school_id)
    if not policy:
        raise ValidationError("Admissions settings changed. Reload the lead.")
    lead = Lead.objects.get(pk=lead.pk, school_id=policy.profile.school_id)
    return prepare_draft(case_for_lead(policy, lead, create=True))


@transaction.atomic
def prepare_draft(case):
    from core.services.config_loader import load_school_config
    from core.views_school_common import _build_lead_prefill_data
    SchedulingProfile.objects.select_for_update().get(pk=case.policy.profile_id)
    case = cases(case.policy).get(pk=case.pk)
    reason = application_reason(case)
    if reason:
        raise ValidationError(reason)
    config = load_school_config(case.lead.school.slug)
    prefill = _build_lead_prefill_data(case.lead, config.raw)
    if case.draft_id:
        draft = case.draft
    else:
        draft = DraftSubmission.objects.filter(school=case.lead.school, lead=case.lead, submitted_at__isnull=True).first()
        if not draft:
            draft = DraftSubmission.objects.create(school=case.lead.school, lead=case.lead,
                email=case.lead.email, data=prefill)
        case.draft = draft
        case.save(update_fields=["draft"])
    # Fill missing lead fields, but never overwrite saved family answers.
    draft.data = {**prefill, **(draft.data or {})}
    draft.extend_expiry()
    draft.save(update_fields=["data", "token_expires_at", "updated_at"])
    return draft


def record_submission(request, draft, submission):
    """Called inside enrollment's create transaction; never uses an email match."""
    case = getattr(request, "booking_admission", None)
    if case is None:
        return
    if not draft or case.draft_id != draft.pk or case.lead_id != draft.lead_id or submission.school_id != case.lead.school_id:
        raise ValidationError("Application identity changed. Reopen your application link.")
    if application_reason(case):
        raise ValidationError(application_reason(case))
    case.submission = submission
    case.save(update_fields=["submission"])
    lead = Lead.objects.select_for_update().get(pk=case.lead_id, school_id=submission.school_id)
    if lead.converted_submission_id:
        raise ValidationError("An application already exists for this lead.")
    before = {"status": lead.status, "converted_submission": None}
    lead.converted_submission = submission
    lead.converted_at = timezone.now()
    lead.status = "enrolled"  # Existing enrollment storage; admissions display derives from submission.
    lead.save(update_fields=["converted_submission", "converted_at", "status", "updated_at"])
    _audit(None, case.policy.profile, lead, before, "change")
    if case.tour_id:
        from .visit_access import notice
        notice(case.tour, "Application received")


def family_context(submission):
    case = AdmissionCase.objects.select_related("policy__profile__school", "submission").filter(submission=submission,
        lead__school_id=submission.school_id, policy__profile__school_id=submission.school_id).first()
    if not case:
        return None
    booking = case.playdates.exclude(state="canceled").select_related("slot__session__room", "slot__program__offering__location").order_by("-created_at").first()
    reason = playdate_reason(case)
    from .models import VisitPilotSettings
    recovery_url = reverse("scheduling:visit_recover", kwargs={"school_slug": submission.school.slug}) if VisitPilotSettings.objects.filter(profile=case.policy.profile).exists() else ""
    return {"case": case, "booking": booking, "message": reason, "allowed": not reason,
        "recovery_url": recovery_url,
        "url": reverse("scheduling:playdates", kwargs={"school_slug": submission.school.slug, "token": submission.status_token})}


def require_playdate(case, profile):
    case = get_object_or_404(cases(case.policy), pk=case.pk, policy__profile=profile,
        submission__school_id=profile.school_id, lead__school_id=profile.school_id)
    # Serialize against enrollment admins changing this application's status.
    case.submission = Submission.objects.select_for_update().get(pk=case.submission_id)
    reason = playdate_reason(case)
    if reason:
        raise ValidationError(reason)
    return case


def playdate_identity(case):
    return {"contact_name": case.lead.name, "contact_email": case.lead.email,
        "child_name": case.submission.student_display_name()}


def playdate_state(item, user):
    case = cases(item.admission_case.policy).get(pk=item.admission_case_id)
    submission = Submission.objects.select_for_update().get(pk=case.submission_id, school_id=case.lead.school_id)
    statuses = item.reservation.snapshot["admission_statuses"]
    if submission.status == statuses["scheduled"]:
        before = {"status": submission.status}
        submission.status = statuses["completed"] if item.state == "attended" else item.prior_submission_status
        submission.save(update_fields=["status"])
        _audit(user, case.policy.profile, submission, before, "change")
