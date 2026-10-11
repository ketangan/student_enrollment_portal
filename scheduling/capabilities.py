"""Operator-granted product access, separate from staff roles and intake switches."""

from django.core.exceptions import ValidationError
from django.http import Http404


CAPABILITIES = {
    "workshops": "Workshops",
    "parties": "Private parties",
    "reservations": "Reservations & payments",
    "camps": "Camps",
    "lessons": "Lessons",
    "integrations": "Integrations",
    "visits": "Tours & visits",
}
DEPENDENCIES = {
    "parties": {"workshops"},
    "reservations": {"workshops"},
    "camps": {"workshops", "reservations"},
}


def validate_capabilities(value):
    if not isinstance(value, list) or any(not isinstance(key, str) or key not in CAPABILITIES for key in value):
        raise ValidationError("Choose only the available Bookings features.")
    if len(value) != len(set(value)):
        raise ValidationError("Choose each feature once.")
    for key in value:
        missing = DEPENDENCIES.get(key, set()) - set(value)
        if missing:
            raise ValidationError(f"{CAPABILITIES[key]} requires {', '.join(CAPABILITIES[item] for item in sorted(missing))}.")


def has_capability(profile, key):
    try:
        validate_capabilities(profile.capabilities)
    except ValidationError:
        return False
    return key in profile.capabilities


def require_capability(profile, key):
    if not has_capability(profile, key):
        raise Http404("Page not found")


def validate_removal(profile, selected):
    # Keep existing commitments manageable. Intake switches pause new work instead.
    from .models import AdmissionPolicy, Camp, IntegrationConnection, LessonService, LessonTeacher, LessonHousehold, LessonClosure, Offering, PartyRequest, Reservation, VisitProgram

    removed = set(profile.capabilities) - set(selected)
    history = {
        "integrations": lambda: IntegrationConnection.objects.filter(profile=profile).exists(),
        "workshops": lambda: Offering.objects.filter(location__profile=profile, visit_program__isnull=True).exists(),
        "visits": lambda: VisitProgram.objects.filter(offering__location__profile=profile).exists()
            or AdmissionPolicy.objects.filter(profile=profile).exists(),
        "parties": lambda: PartyRequest.objects.filter(offering__location__profile=profile).exists()
            or Offering.objects.filter(location__profile=profile, accepts_party_requests=True).exists(),
        "reservations": lambda: Reservation.objects.filter(profile=profile, visit_booking__isnull=True).exists(),
        "camps": lambda: Camp.objects.filter(offering__location__profile=profile).exists(),
        "lessons": lambda: any(model.objects.filter(profile=profile).exists()
            for model in (LessonService, LessonTeacher, LessonHousehold))
            or LessonClosure.objects.filter(location__profile=profile).exists(),
    }
    for key in sorted(removed):
        if key in history and history[key]():
            raise ValidationError(f"Keep {CAPABILITIES[key]} enabled: this business has saved setup or records. Pause new requests or bookings in that feature instead.")
