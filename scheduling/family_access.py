"""Owner-attested guardian scope; one-use bearer invitation exchanged for a session."""
import hashlib
import json
import re
import secrets
import uuid
from datetime import timedelta

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from .family_models import FamilyScheduleGrant
from .lesson_models import LessonHousehold, LessonStudent
from .lessons import audit, lesson_access
from .models import SchedulingProfile

SALT = "scheduling.family-invite.v1"
SESSION_KEY = "bookings_family_access"
TOKEN_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
ACCESS_ERROR = "This access link is unavailable. Please ask the studio for a new link."


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class InviteForm(forms.Form):
    recipient_name = forms.CharField(max_length=100, label="Guardian name")
    recipient_email = forms.EmailField(max_length=254, label="Guardian email")
    students = forms.ModelMultipleChoiceField(queryset=LessonStudent.objects.none(),
        widget=forms.CheckboxSelectMultiple, label="Students this guardian may view")
    days = forms.TypedChoiceField(coerce=int, initial=7, label="Access expires after",
        choices=[(1, "1 day"), (7, "7 days"), (30, "30 days")])
    verified = forms.BooleanField(label="I have verified this recipient may view these students' schedules.")
    allow_booking = forms.BooleanField(required=False, label="Also allow this guardian to book single lessons for these students")
    invite_key = forms.CharField(max_length=1024, widget=forms.HiddenInput)

    def __init__(self, *args, user, household, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["students"].queryset = household.students.all()
        self.fields["invite_key"].initial = signing.dumps({"user": user.pk, "household": household.pk,
            "profile": household.profile_id, "key": str(uuid.uuid4())}, salt=SALT)


class RevokeForm(forms.Form):
    reason = forms.CharField(max_length=200)
    confirm = forms.BooleanField(label="Revoke this guardian's access")


@sensitive_variables()
@transaction.atomic
def issue_invitation(user, slug, household_pk, data):
    profile, _ = lesson_access(user, slug, "setup.edit", locked=True)
    household = get_object_or_404(LessonHousehold, profile=profile, pk=household_pk)
    form = InviteForm(data, user=user, household=household)
    if not form.is_valid():
        raise ValidationError("Check the guardian, students and confirmation before creating access.")
    try:
        token = signing.loads(form.cleaned_data["invite_key"], salt=SALT, max_age=3600)
        key = uuid.UUID(token["key"])
        if {k: token[k] for k in ["user", "household", "profile"]} != {
            "user": user.pk, "household": household.pk, "profile": profile.pk}:
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError) as exc:
        raise ValidationError("This form expired. Reload before creating access.") from exc
    clean = form.cleaned_data
    student_ids = sorted(clean["students"].values_list("pk", flat=True))
    payload = digest(json.dumps([clean["recipient_name"], clean["recipient_email"], student_ids, clean["days"], clean["allow_booking"]]))
    previous = FamilyScheduleGrant.objects.filter(submission_key=key).first()
    if previous:
        if previous.household_id != household.pk or previous.submission_digest != payload:
            raise ValidationError("This form was already used. Reload before creating different access.")
        return previous, None
    now = timezone.now()
    secret = secrets.token_urlsafe(32)
    grant = FamilyScheduleGrant.objects.create(household=household, recipient_name=clean["recipient_name"],
        recipient_email=clean["recipient_email"], verified_by=user, created_at=now,
        expires_at=now + timedelta(days=clean["days"]), token_expires_at=now + timedelta(days=1),
        token_hash=digest(secret), submission_key=key, submission_digest=payload, allow_booking=clean["allow_booking"])
    grant.students.set(student_ids)
    audit(user, profile, grant, "create", {"family_access": "issued", "student_ids": student_ids,
        "expires_at": grant.expires_at.isoformat()})
    return grant, secret


@transaction.atomic
def revoke_access(user, slug, public_id, data):
    profile, _ = lesson_access(user, slug, "setup.edit", locked=True)
    grant = get_object_or_404(FamilyScheduleGrant, household__profile=profile, public_id=public_id)
    form = RevokeForm(data)
    if not form.is_valid():
        raise ValidationError("Enter a reason and confirm revocation.")
    if not grant.revoked_at:
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        audit(user, profile, grant, "change", {"family_access": "revoked", "reason": form.cleaned_data["reason"]})
    return grant


def public_profile(slug, locked=False):
    objects = SchedulingProfile.objects
    if locked:
        objects = objects.select_for_update()
    from .capabilities import require_capability
    profile = get_object_or_404(objects, school__slug=slug, enabled=True, lessons_enabled=True)
    require_capability(profile, "lessons")
    return profile


def authorized_students(grant):
    # Fixed student scope: new siblings and students moved out of the household never inherit access.
    return grant.students.filter(household_id=grant.household_id)


@sensitive_variables()
@transaction.atomic
def redeem_invitation(slug, secret, session):
    profile = public_profile(slug, locked=True)
    if not TOKEN_PATTERN.fullmatch(secret):
        raise ValidationError(ACCESS_ERROR)
    now = timezone.now()
    grant = FamilyScheduleGrant.objects.filter(household__profile=profile, token_hash=digest(secret),
        redeemed_at__isnull=True, revoked_at__isnull=True, token_expires_at__gt=now, expires_at__gt=now).first()
    if not grant or not authorized_students(grant).exists():
        raise ValidationError(ACCESS_ERROR)
    session_secret = secrets.token_urlsafe(32)
    grant.session_hash, grant.redeemed_at = digest(session_secret), now
    grant.save(update_fields=["session_hash", "redeemed_at"])
    audit(None, profile, grant, "change", {"family_access": "opened"})
    # Use Django's existing session, rotating its ID without granting any staff identity.
    session.cycle_key()
    session[SESSION_KEY] = {**session.get(SESSION_KEY, {}), str(profile.pk): session_secret}
    return grant


@sensitive_variables()
def family_grant(slug, session):
    profile = public_profile(slug)
    secret = session.get(SESSION_KEY, {}).get(str(profile.pk), "")
    if not TOKEN_PATTERN.fullmatch(secret):
        raise Http404
    grant = get_object_or_404(FamilyScheduleGrant, household__profile=profile,
        session_hash=digest(secret), redeemed_at__isnull=False, revoked_at__isnull=True, expires_at__gt=timezone.now())
    if not authorized_students(grant).exists():
        raise Http404
    return profile, grant


@sensitive_variables()
@transaction.atomic
def close_family_access(slug, session):
    profile = public_profile(slug, locked=True)
    secret = session.get(SESSION_KEY, {}).get(str(profile.pk), "")
    if TOKEN_PATTERN.fullmatch(secret):
        grant = FamilyScheduleGrant.objects.filter(household__profile=profile, session_hash=digest(secret), revoked_at__isnull=True).first()
        if grant:
            grant.revoked_at = timezone.now()
            grant.save(update_fields=["revoked_at"])
            audit(None, profile, grant, "change", {"family_access": "signed_out"})
    remaining = dict(session.get(SESSION_KEY, {}))
    remaining.pop(str(profile.pk), None)
    session[SESSION_KEY] = remaining
