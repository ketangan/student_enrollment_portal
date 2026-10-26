"""Staff-entered custom parties feed the existing inquiry review and payment flow."""
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
from .parties import audit_party
from .party_forms import PartyInquiryForm
from .requests import submission_digest

TOKEN_SALT = "scheduling.staff-party.v1"


class StaffPartyForm(PartyInquiryForm):
    offering = forms.ModelChoiceField(label="Workshop / location", queryset=Offering.objects.none(),
        empty_label="Choose a workshop and location")
    occasion = forms.CharField(label="Occasion", max_length=150, required=False)
    age_range = forms.CharField(label="Participants' ages", max_length=100, required=False)
    request_token = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, profile, **kwargs):
        # Custom requests are reviewed by staff, never converted to direct package bookings here.
        super().__init__(*args, **kwargs)
        self.fields["offering"].queryset = Offering.objects.filter(
            location__in=staff_locations(profile), accepts_party_requests=True,
            visit_program__isnull=True).select_related("location").order_by("location__name", "name", "pk")
        self.fields["offering"].label_from_instance = lambda item: f"{item.name} ({item.location.name})"
        self.fields["organizer_name"].label = "Customer name"
        self.fields["starts_at"].label = "Preferred date and time (studio time)"
        self.fields["customer_notes"].label = "Customer requests"
        self.order_fields(["offering", "starts_at", "organizer_name", "organizer_email",
            "organizer_phone", "group_size", "space", "occasion", "age_range", "customer_notes", "request_token"])

    def full_clean(self):
        # Parse the entered wall-clock time at the authorized studio, not the server timezone.
        try:
            offering = self.fields["offering"].clean(self.data.get("offering"))
        except ValidationError:
            offering = None
        with timezone.override(ZoneInfo(offering.location.timezone_name) if offering else timezone.get_default_timezone()):
            super().full_clean()


def token_for(user, profile):
    return signing.dumps({"user": user.pk, "profile": profile.pk, "key": str(uuid.uuid4())}, salt=TOKEN_SALT)


@transaction.atomic
def create(user, slug, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="parties")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="parties")
    form = StaffPartyForm(data, profile=profile)
    if not form.is_valid():
        return None, form
    try:
        token = signing.loads(form.cleaned_data["request_token"], salt=TOKEN_SALT, max_age=7200)
        key = uuid.UUID(token["key"])
        if token["user"] != user.pk or token["profile"] != profile.pk:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        form.add_error(None, "This form has expired. Open a new party request and try again.")
        return None, form
    stamp = submission_digest(data)
    existing = PartyRequest.objects.filter(submission_key=key,
        offering__location__profile=profile).first()
    if existing:
        if existing.submission_digest != stamp:
            form.add_error(None, "This request was already saved with different details. Open the saved request.")
            return None, form
        return existing, form
    values = form.inquiry_values()
    values.pop("request_token")
    offering = values.pop("offering")
    item = PartyRequest(offering=offering, **values, submission_key=key,
        submission_digest=stamp, browser_digest=digest({"staff": user.pk, "profile": profile.pk}),
        offering_snapshot={"workshop": offering.name, "location": offering.location.name,
            "timezone": offering.location.timezone_name})
    item.full_clean()
    item.save()
    audit_party(user, profile, item, {"created_by_staff": True, "status": {"from": None, "to": item.status}}, created=True)
    return item, form
