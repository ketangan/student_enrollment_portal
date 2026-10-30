"""Authorized staff intake reuses customer package validation and reservations."""
import uuid
from zoneinfo import ZoneInfo

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .access import require_location_access, staff_locations
from .lessons import digest
from .models import Offering, PartyRequest, SchedulingProfile
from .parties import audit_party, inquiry_digest, reserve_package
from .party_forms import PartyInquiryForm
from .requests import submission_digest
from .participant_policy import AgeRangeField, age_policy
from .party_policy import party_policy

TOKEN_SALT = "scheduling.staff-party.v1"


class StaffPartyForm(PartyInquiryForm):
    duration_minutes = forms.IntegerField(label="Duration (minutes)", min_value=15, max_value=720,
        widget=forms.NumberInput(attrs={"step": "1", "inputmode": "numeric"}))
    offering = forms.ModelChoiceField(label="Workshop / location", queryset=Offering.objects.none(),
        empty_label="Choose a workshop and location")
    occasion = forms.CharField(label="Occasion", max_length=150, required=False)
    age_range = forms.CharField(label="Participants' ages", max_length=100, required=False)
    request_token = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, profile, **kwargs):
        packages_only = party_policy(profile.school)["packages_only"]
        offerings = Offering.objects.filter(location__in=staff_locations(profile), accepts_party_requests=True,
            visit_program__isnull=True).select_related("location__profile__school").order_by("location__name", "name", "pk")
        if packages_only:
            offerings = offerings.filter(is_published=True, party_packages__is_published=True,
                party_packages__booking_mode="direct").distinct()
        data = args[0] if args else kwargs.get("data")
        selected = (data if data is not None else kwargs.get("initial", {})).get("offering")
        offering = offerings.filter(pk=selected).first() if str(selected or "").isdecimal() else None
        super().__init__(*args, offering=offering if packages_only else None, **kwargs)
        self.packages_only = packages_only
        self.selected_offering = offering
        if packages_only:
            self.fields.pop("duration_minutes")
        policy = age_policy(profile.school)
        self.fields["age_range"] = AgeRangeField(minimum=policy["minimum"] if policy else None,
            label="Participants' ages", max_length=100, required=False,
            widget=forms.TextInput(attrs={"placeholder": "e.g. 7-9 or Adults"}))
        self.fields["offering"].queryset = offerings
        self.fields["offering"].label_from_instance = lambda item: f"{item.name} ({item.location.name})"
        self.fields["organizer_name"].label = "Customer name"
        self.fields["starts_at"].label = "Preferred date and time (studio time)"
        self.fields["customer_notes"].label = "Customer requests"
        self.order_fields(["offering", "starts_at", "duration_minutes", "organizer_name", "organizer_email",
            "organizer_phone", "group_size", "participant_estimate", "space", "occasion", "age_range", "customer_notes", "request_token"])

    def full_clean(self):
        # Parse the entered wall-clock time at the authorized studio, not the server timezone.
        try:
            offering = self.fields["offering"].clean(self.data.get("offering"))
        except ValidationError:
            offering = None
        with timezone.override(ZoneInfo(offering.location.timezone_name) if offering else timezone.get_default_timezone()):
            super().full_clean()

    def clean(self):
        data = super().clean()
        if self.packages_only and not data.get("package") and "package" not in self.errors:
            raise ValidationError("Choose a published package and an offered time.")
        return data


def token_for(user, profile, offering=None):
    return signing.dumps({"user": user.pk, "profile": profile.pk, "key": str(uuid.uuid4()),
        "configuration": inquiry_digest(offering) if offering else None}, salt=TOKEN_SALT)


@transaction.atomic
def create(user, slug, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="parties")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="parties")
    form = StaffPartyForm(data, profile=profile)
    try:
        token = signing.loads(data.get("request_token", ""), salt=TOKEN_SALT, max_age=7200)
        key = uuid.UUID(token["key"])
        if token["user"] != user.pk or token["profile"] != profile.pk:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        form.add_error(None, "This form has expired. Open a new party request and try again.")
        return None, form
    stamp = submission_digest(data)
    existing = PartyRequest.objects.filter(submission_key=key,
        offering__location__profile=profile).select_related("offering").first()
    if existing:
        if existing.submission_digest != stamp or not staff_locations(profile).filter(pk=existing.offering.location_id).exists():
            form.add_error(None, "This request was already saved with different details. Open the saved request.")
            return None, form
        return existing, form
    if not form.is_valid():
        return None, form
    if form.packages_only and token.get("configuration") != inquiry_digest(form.cleaned_data["offering"]):
        form.add_error(None, "The package or offered times changed. Open a new party booking.")
        return None, form
    values = form.inquiry_values()
    values.pop("request_token")
    offering = values.pop("offering")
    item = PartyRequest(offering=offering, **values, submission_key=key,
        submission_digest=stamp, browser_digest=digest({"staff": user.pk, "profile": profile.pk}),
        offering_snapshot={"workshop": offering.name, "location": offering.location.name,
            "timezone": offering.location.timezone_name})
    item.full_clean()
    item.save()
    if form.packages_only:
        reserve_package(item, user=user)
    audit_party(user, profile, item, {"created_by_staff": True, "status": {"from": None, "to": item.status}}, created=True)
    return item, form
