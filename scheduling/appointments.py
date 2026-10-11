"""Scoped public/family booking, using the same inventory lock, planner and ledger."""
import secrets
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.payments import service as payments
from .appointment_forms import OfferForm, TrialContactForm, OfflineLessonPaymentForm
from .appointment_models import LessonBookingOffer, LessonBooking, LessonBookingNotice
from .family_access import authorized_students, digest as secret_hash, family_grant, public_profile, TOKEN_PATTERN
from .lesson_models import LessonHousehold, LessonStudent, LessonSeries, LessonOccurrence
from .lessons import SlotCalendar, audit, digest, lesson_access, local_datetime, plan_dates, preview_revision

SALT = "scheduling.single-lesson.v1"
BROWSER_KEY = "lesson_booking_browser"
PREVIEW_ERROR = "This time or offer changed. Choose a time again; nothing new was booked."


def preview_enabled():
    # Real delivery, guest verification and online settlement are separate rollout gates.
    return settings.DEBUG and getattr(settings, "BOOKINGS_LESSON_BOOKING_PREVIEW", False)


def booking_profile(slug, locked=False):
    if not preview_enabled():
        raise Http404
    return public_profile(slug, locked)


def offers(profile, family=False):
    return LessonBookingOffer.objects.filter(profile=profile, published=True, service__is_active=True,
        teacher__is_active=True, teacher__services=F("service_id"), **{"family_booking" if family else "public_trials": True}).select_related(
        "service", "teacher", "location", "room", "profile__school")


def get_offer(profile, public_id, family=False):
    offer = get_object_or_404(offers(profile, family), public_id=public_id)
    try:
        offer.full_clean()
    except ValidationError as exc:
        raise Http404 from exc
    return offer


@transaction.atomic
def save_offer(user, slug, data, pk=None):
    profile, _ = lesson_access(user, slug, "setup.edit", locked=True)
    instance = get_object_or_404(LessonBookingOffer, profile=profile, pk=pk) if pk else None
    form = OfferForm(data, instance=instance, profile=profile)
    if form.is_valid():
        instance = form.save()
        audit(user, profile, instance, "change" if pk else "add", {"offer_fields": sorted(form.changed_data)})
    return form


def browser_secret(session):
    value = session.get(BROWSER_KEY, "")
    if not TOKEN_PATTERN.fullmatch(value):
        value = secrets.token_urlsafe(32)
        session[BROWSER_KEY] = value
    return value


def booking_scope(slug, session, family=False, locked=False):
    profile = booking_profile(slug, locked)
    grant = None
    if family:
        profile, grant = family_grant(slug, session)
        if not grant.allow_booking:
            raise Http404
    return profile, grant


def slot_data(offer, student=None):
    return dict(service=offer.service, teacher=offer.teacher, location=offer.location, room=offer.room, student=student)


def validate_day(offer, day):
    today = timezone.localdate(timezone=ZoneInfo(offer.location.timezone_name))
    if not today <= day <= today + timedelta(days=offer.horizon_days):
        raise ValidationError(f"Choose a date between {today:%b %d} and {today + timedelta(days=offer.horizon_days):%b %d}.")


def day_slots(offer, day, student=None):
    validate_day(offer, day)
    data = slot_data(offer, student)
    zone = ZoneInfo(offer.location.timezone_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    calendar = SlotCalendar(data, start, start + timedelta(days=1))
    threshold = timezone.now() + timedelta(hours=offer.notice_hours)
    result = []
    # At most 96 candidates per request. Ambiguous/nonexistent wall times never appear.
    for minute in range(0, 1440, offer.interval_minutes):
        wall = datetime.combine(day, time.min) + timedelta(minutes=minute)
        try:
            begins = local_datetime(day, wall.time(), zone)
            if begins < threshold:
                continue
            row = plan_dates(data, [wall], calendar=calendar)[0]
        except ValidationError:
            continue
        if row["state"] == "scheduled":
            result.append(row)
    return result


def quote(offer, row, student, grant, browser):
    data = slot_data(offer, student)
    return signing.dumps({"offer": str(offer.public_id), "profile": offer.profile_id,
        "start": row["starts_at"].isoformat(), "key": str(uuid.uuid4()), "browser": secret_hash(browser),
        "student": student.pk if student else None, "grant": grant.pk if grant else None,
        "revision": preview_revision(data, [row]), "offer_revision": offer_revision(offer)}, salt=SALT)


def offer_revision(offer):
    return digest({field.attname: field.value_from_object(offer) for field in offer._meta.concrete_fields})


def read_quote(token, profile, browser, grant):
    try:
        data = signing.loads(token, salt=SALT, max_age=1800)
        uuid.UUID(data["key"])
        uuid.UUID(data["offer"])
        if (data["profile"], data["browser"], data["grant"]) != (
                profile.pk, secret_hash(browser), grant.pk if grant else None):
            raise ValueError
        datetime.fromisoformat(data["start"])
        return data
    except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
        raise ValidationError(PREVIEW_ERROR) from exc


def booking_contact(grant, student, raw):
    if grant:
        return dict(contact_name=grant.recipient_name, contact_email=grant.recipient_email, student_name=student.name)
    form = TrialContactForm(raw)
    if not form.is_valid():
        raise ValidationError("Enter the contact and student details, and confirm your authority to book.")
    return {k: form.cleaned_data[k] for k in ("contact_name", "contact_email", "student_name")}


@transaction.atomic
def confirm_booking(slug, session, token, raw, family=False, expected_offer=None):
    profile, grant = booking_scope(slug, session, family, locked=True)
    browser = browser_secret(session)
    signed = read_quote(token, profile, browser, grant)
    if expected_offer is not None and str(expected_offer) != signed["offer"]:
        raise ValidationError(PREVIEW_ERROR)
    student = get_object_or_404(authorized_students(grant), pk=signed["student"]) if grant else None
    contact = booking_contact(grant, student, raw)
    payload = digest({"quote": signed, "contact": contact})
    existing = LessonBooking.objects.filter(series__profile=profile, series__submission_key=signed["key"]).first()
    if existing:
        if existing.series.submission_digest != payload:
            raise ValidationError(PREVIEW_ERROR)
        return existing
    offer = get_offer(profile, signed["offer"], family)
    wall = datetime.fromisoformat(signed["start"]).astimezone(ZoneInfo(offer.location.timezone_name))
    validate_day(offer, wall.date())
    row = plan_dates(slot_data(offer, student), [wall.replace(tzinfo=None)])[0]
    if (row["state"] != "scheduled" or row["starts_at"] < timezone.now() + timedelta(hours=offer.notice_hours)
            or (wall.hour * 60 + wall.minute) % offer.interval_minutes or wall.second or wall.microsecond
            or offer_revision(offer) != signed["offer_revision"]
            or preview_revision(slot_data(offer, student), [row]) != signed["revision"]):
        raise ValidationError(PREVIEW_ERROR)
    if student is None:
        # A matching email is not identity verification. Never merge into an existing family.
        household = LessonHousehold.objects.create(profile=profile, name=contact["contact_name"], email=contact["contact_email"])
        student = LessonStudent.objects.create(household=household, name=contact["student_name"])
    data = slot_data(offer, student)
    series = LessonSeries.objects.create(profile=profile, **data, first_date=wall.date(), last_date=wall.date(),
        start_time=wall.time().replace(tzinfo=None), interval_weeks=1, submission_key=signed["key"], submission_digest=payload,
        snapshot={"service": offer.service.name, "subject": offer.service.subject, "duration_minutes": offer.service.duration_minutes,
            "teacher": offer.teacher.name, "student": student.name, "household": student.household.name,
            "location": offer.location.name, "timezone": offer.location.timezone_name, "room": offer.room.name if offer.room else "",
            "buffer_minutes": offer.teacher.buffer_minutes, "travel_minutes": offer.teacher.travel_minutes,
            "booking_offer": offer.title, "booking_price_cents": offer.fee_cents,
            "makeup_eligible": offer.makeup_eligible})
    LessonOccurrence.objects.create(series=series, **row)
    order = payments.create_order(school=profile.school, purpose="lesson_booking", source_key=str(series.public_id),
        total_cents=offer.fee_cents, deposit_cents=0, description=offer.title) if offer.fee_cents else None
    booking = LessonBooking.objects.create(offer=offer, series=series, order=order, grant=grant,
        contact_name=contact["contact_name"], contact_email=contact["contact_email"], browser_hash=secret_hash(browser))
    save_notice(booking, "confirmed", "Lesson booked")
    audit(None, profile, booking, "add", {"lesson_booking": "family" if grant else "public_trial", "grant_id": grant.pk if grant else None})
    return booking


def current_series(series):
    current = series
    while current.replacements.exists():
        current = current.replacements.latest("created_at")
    return current


def source_booking(series):
    while series.predecessor_id:
        series = series.predecessor
    return LessonBooking.objects.filter(series=series).select_related("series", "order").first()


def save_change_notice(series, subject):
    booking = source_booking(series)
    if booking:
        item = current_series(series).occurrences.first()
        save_notice(booking, f"lesson:{item.pk}:{item.version}", subject)


@transaction.atomic
def save_notice(booking, event_key, subject):
    current = current_series(booking.series)
    item = current.occurrences.first()
    stamp = item.starts_at.astimezone(ZoneInfo(current.snapshot["timezone"]))
    snapshot = current.snapshot
    amount = booking.order.total_cents if booking.order_id else 0
    body = (f"Hello {booking.contact_name},\n\n{subject}: {snapshot['student']} - {snapshot['service']}\n"
        f"{stamp:%A, %B %d, %Y at %I:%M %p}\n{snapshot['teacher']}\n{snapshot['location']}"
        f"{(' / ' + snapshot['room']) if snapshot['room'] else ''}\nStatus: {item.get_state_display()}\n"
        f"Booking reference: {booking.public_id}\n"
        + (f"Quoted price: ${amount / 100:.2f}. Contact the studio about payment or refunds.\n" if amount else "No charge.\n")
        + "Contact the studio to change or cancel your lesson.\n")
    notice, created = LessonBookingNotice.objects.get_or_create(booking=booking, event_key=event_key,
        defaults={"recipient": booking.contact_email, "subject": subject, "body": body})
    if created:
        capture_notice(notice)
    return notice


@transaction.atomic
def capture_notice(notice):
    from .messaging import capture
    group = f"lesson:{notice.booking.public_id}"
    notice.delivery = capture(notice.booking.series.profile, event_key=f"{group}:{notice.event_key}",
        group_key=group, recipient=notice.recipient, subject=notice.subject, body=notice.body)
    notice.save(update_fields=["delivery"])
    return notice.delivery


def customer_booking(slug, session, public_id):
    profile = booking_profile(slug)
    booking = get_object_or_404(LessonBooking.objects.select_related("series", "offer", "order"),
        series__profile=profile, public_id=public_id)
    if booking.grant_id:
        _, grant = family_grant(slug, session)
        if grant.pk != booking.grant_id or not authorized_students(grant).filter(pk=booking.series.student_id).exists():
            raise Http404
    elif not secrets.compare_digest(booking.browser_hash, secret_hash(session.get(BROWSER_KEY, ""))):
        raise Http404
    return booking


@transaction.atomic
def record_payment(user, slug, public_id, raw):
    profile, _ = lesson_access(user, slug, "sessions.edit", locked=True)
    if not preview_enabled():
        raise Http404
    booking = get_object_or_404(LessonBooking, series__profile=profile, public_id=public_id, order__isnull=False)
    form = OfflineLessonPaymentForm(raw)
    if not form.is_valid():
        raise ValidationError("Check the test payment amount, reference and confirmation.")
    data = form.cleaned_data
    if (data["kind"] == "payment" and current_series(booking.series).occurrences.get().state == "canceled"
            and not booking.order.entries.filter(key=data["key"]).exists()):
        raise ValidationError("This lesson was canceled. Do not collect another payment.")
    return payments.record_offline(order=booking.order, actor=user, key=data["key"], kind=data["kind"],
        amount_cents=int(data["amount"] * 100), reference=data["reference"])
