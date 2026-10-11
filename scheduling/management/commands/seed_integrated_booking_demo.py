"""Repeatable fictional fixtures, restricted to the isolated local preview database."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.http import QueryDict
from django.utils import timezone

from core.models import School, SchoolAdminMembership, SchoolProgram
from core.payments.models import PaymentAccount
from scheduling import lessons, parties, requests, visits
from scheduling.capabilities import CAPABILITIES
from scheduling.lesson_forms import SeriesForm
from scheduling.models import (
    AdmissionPolicy, Camp, ChoiceGroup, ChoiceOption, LessonAvailability, LessonBookingOffer,
    LessonCreditPolicy, LessonHousehold, LessonSeries, LessonService, LessonStudent, LessonTeacher,
    Location, Offering, PartyRequest, Reservation, Room, SchedulingMembership, SchedulingProfile,
    Session, VisitBooking, VisitPilotSettings, VisitProgram, VisitSlot, WorkshopRequest,
)

SLUG = "enrollment-request-demo"
USERNAME = "demo_admin"
PREVIEW_DATABASE = "pontora_bookings_m1a_preview"


def saved(model, defaults=None, **lookup):
    return model.objects.get_or_create(**lookup, defaults=defaults or {})[0]


class Command(BaseCommand):
    help = "Prepare Enrollment Request Demo for local integrated Bookings testing. Never runs against customer databases."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if not settings.DEBUG or database["NAME"] != PREVIEW_DATABASE or database.get("HOST") not in {"127.0.0.1", "localhost"}:
            raise CommandError("Only the isolated loopback Bookings preview database is allowed.")
        school = saved(School, slug=SLUG, defaults={"display_name": "Riverside Learning Academy", "is_demo": True,
            "plan": "custom", "program_field_key": "interested_in",
            "feature_flags": {"email_notifications_enabled": False, "ai_summary_enabled": False}})
        if not school.is_demo or school.stripe_customer_id or school.smtp_host or school.app_fee_stripe_secret_key:
            raise CommandError("Refusing to modify a non-demo school or one with external billing/email configuration.")
        user = saved(get_user_model(), username=USERNAME, defaults={"first_name": "Alex", "last_name": "Demo", "is_staff": True})
        if user.is_superuser or SchoolAdminMembership.objects.filter(user=user).exclude(school=school).exists() or SchedulingMembership.objects.filter(user=user).exclude(profile__school=school).exists():
            raise CommandError("Refusing to change an operator or shared account password.")
        user.is_active = user.is_staff = True
        user.set_password(options["password"])
        user.save(update_fields=["password", "is_active", "is_staff"])
        saved(SchoolAdminMembership, school=school, user=user, defaults={"role": "owner"})
        for code, name in [("creative", "Creative Learning"), ("music", "Music Foundations")]:
            saved(SchoolProgram, school=school, code=code, defaults={"name": name})
        profile = saved(SchedulingProfile, school=school, defaults={"enabled": True, "capabilities": list(CAPABILITIES),
            "native_reservations_enabled": True, "lessons_enabled": True, "message_preview_enabled": True})
        host = saved(SchedulingMembership, profile=profile, user=user, defaults={"role": "owner"})
        account = saved(PaymentAccount, school=school, defaults={"reference": "acct_local_preview_enrollment", "enabled": True})
        if account.reference != "acct_local_preview_enrollment":
            raise CommandError("Only the simulated local payment account is permitted.")
        location = saved(Location, profile=profile, name="Main Campus", defaults={"address": "100 Example Lane",
            "timezone_name": "America/Los_Angeles"})
        rooms = {name: saved(Room, location=location, name=name, defaults={"capacity": capacity}) for name, capacity in
            [("Welcome Room", 8), ("Creative Studio", 12), ("Piano Room", 2), ("Camp Room", 10)]}
        # Keep the original fixture dates on a rerun, preserving the user's subsequent practice edits.
        existing = Session.objects.filter(offering__location=location).order_by("pk").first()
        today = timezone.localdate(timezone=ZoneInfo(location.timezone_name))
        first = today + timedelta(days=1)
        while first.weekday() > 4:
            first += timedelta(days=1)
        self.first = timezone.localtime(existing.starts_at, ZoneInfo(location.timezone_name)).date() if existing else first
        self.location, self.rooms, self.profile, self.user = location, rooms, profile, user
        self.prepare_visits(host)
        self.prepare_workshops()
        self.prepare_lessons(host)
        self.stdout.write(self.style.SUCCESS("Local integrated demo ready: /schools/enrollment-request-demo/admin/ | demo_admin"))

    def session(self, offering, day, hour, room, capacity, minutes=60):
        start = datetime.combine(day, time(hour), ZoneInfo(self.location.timezone_name))
        return saved(Session, offering=offering, starts_at=start, defaults={"ends_at": start + timedelta(minutes=minutes),
            "room": self.rooms[room], "capacity": capacity, "is_published": True})

    def prepare_visits(self, host):
        saved(AdmissionPolicy, profile=self.profile, defaults={"enabled": True, "require_tour": True,
            "eligible_statuses": ["New", "In Review"], "application_release": "automatic", "playdate_release": "staff"})
        saved(VisitPilotSettings, profile=self.profile, defaults={"contact_email": "office@example.invalid", "family_changes": True, "notice_hours": 24})
        for kind, name, hour in [("tour", "Campus tour", 10), ("playdate", "Classroom playdate", 11)]:
            offering = saved(Offering, location=self.location, name=name, defaults={"is_published": True})
            program = saved(VisitProgram, offering=offering, defaults={"kind": kind, "capture_leads": kind == "tour"})
            for week in range(4):
                session = self.session(offering, self.first + timedelta(days=7*week), hour, "Welcome Room", 8, 30)
                saved(VisitSlot, session=session, defaults={"program": program, "host": host})
            if kind == "tour":
                for offset, name, child, email in [
                    (1, "Taylor Walkthrough", "Casey Walkthrough", "taylor-walkthrough@example.invalid"),
                    (2, "Robin Practice", "Quinn Practice", "robin-practice@example.invalid"),
                ]:
                    self.tour_fixture(program, host, offset, name, child, email)

    def tour_fixture(self, program, host, offset, name, child, email):
        if VisitBooking.objects.filter(slot__program=program, contact_email=email).exists():
            return
        session = self.session(program.offering, self.first + timedelta(days=offset), 10, "Welcome Room", 8, 30)
        slot = saved(VisitSlot, session=session, defaults={"program": program, "host": host})
        data = QueryDict(mutable=True)
        data.update({"contact_name": name, "contact_email": email, "child_name": child,
            "adults": "1", "children": "1", "authority": "on", "booking_token": visits.booking_token(slot, "integrated-demo")})
        booking, form = visits.book(SLUG, slot.pk, data, "integrated-demo")
        if booking is None:
            raise CommandError(str(form.errors))
        # An attendance-ready fixture avoids waiting a week to test the application handoff.
        past = timezone.now() - timedelta(hours=2)
        Session.objects.filter(pk=session.pk).update(starts_at=past, ends_at=past+timedelta(minutes=30))
        Reservation.objects.filter(visit_booking=booking).update(starts_at=past, ends_at=past+timedelta(minutes=30), hold_until=past)

    def prepare_workshops(self):
        offering = saved(Offering, location=self.location, name="Creative workshop", defaults={"is_published": True,
            "accepts_requests": True, "accepts_party_requests": True, "description": "Choose a project and color, then create it together."})
        group = saved(ChoiceGroup, offering=offering, name="Project", defaults={"reference_image": "/static/demo/plushumi/assets/designs.jpg"})
        for name in ["Cat", "Bunny"]:
            saved(ChoiceOption, group=group, label=name)
        group = saved(ChoiceGroup, offering=offering, name="Color", defaults={"position": 1})
        for name, color in [("Blue", "#80B8DB"), ("Pink", "#E7AFBF")]:
            saved(ChoiceOption, group=group, label=name, defaults={"swatch": color})
        for offset in [1, 3, 8]:
            self.session(offering, self.first+timedelta(days=offset), 14, "Creative Studio", 12, 90)
        session = offering.sessions.order_by("starts_at").first()
        if not WorkshopRequest.objects.filter(session__offering=offering, purchaser_email="morgan-workshop@example.invalid").exists():
            catalog = requests.catalog_for(offering)
            data = QueryDict(mutable=True)
            data.update({"request_token": requests.request_token(session, catalog, "integrated-demo"),
                "purchaser_name": "Morgan Workshop", "purchaser_email": "morgan-workshop@example.invalid",
                "participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0", "participants-0-name": "Jamie Workshop"})
            for choice in catalog:
                data[f"participants-0-choice_{choice.pk}"] = choice.active_options[0].pk
            item, purchaser, participants = requests.submit_request(SLUG, session.pk, data, "integrated-demo")
            if item is None:
                raise CommandError(str((purchaser.errors, participants.errors)))
        if not PartyRequest.objects.filter(offering=offering, organizer_email="jordan-party@example.invalid").exists():
            data = QueryDict(mutable=True)
            data.update({"request_token": parties.inquiry_token(offering, "integrated-demo"), "organizer_name": "Jordan Party",
                "organizer_email": "jordan-party@example.invalid", "starts_at": f"{self.first+timedelta(days=5)}T11:00",
                "duration_minutes": "120", "group_size": "8", "space": "exclusive", "customer_notes": "Birthday workshop; eight participants."})
            item, form = parties.submit_inquiry(SLUG, offering.pk, data, "integrated-demo")
            if item is None:
                raise CommandError(str(form.errors))
        camp_offering = saved(Offering, location=self.location, name="Creative camp", defaults={"is_published": True})
        dates = [self.session(camp_offering, self.first+timedelta(days=offset), 9, "Camp Room", 10, 180) for offset in [7, 8, 9]]
        camp, created = Camp.objects.get_or_create(offering=camp_offering, name="Three-day creative camp",
            defaults={"price": "150.00", "deposit": "50.00", "online": True, "is_open": True})
        if created:
            camp.sessions.set(dates)

    def prepare_lessons(self, host):
        service = saved(LessonService, profile=self.profile, name="Piano lesson", defaults={"subject": "Piano", "duration_minutes": 30})
        teacher = saved(LessonTeacher, profile=self.profile, name="Alex Demo", defaults={"buffer_minutes": 5, "booking_host": host})
        teacher.services.add(service)
        for weekday in range(5):
            saved(LessonAvailability, teacher=teacher, location=self.location, weekday=weekday, start_time=time(13), end_time=time(18))
        family = saved(LessonHousehold, profile=self.profile, email="park-family@example.invalid", defaults={"name": "Park Family"})
        student = saved(LessonStudent, household=family, name="Riley Park")
        saved(LessonStudent, household=family, name="Jamie Park")
        saved(LessonCreditPolicy, profile=self.profile, defaults={"enabled": True, "notice_hours": 24, "expiry_days": 90})
        if not LessonSeries.objects.filter(profile=self.profile, student=student).exists():
            data = {"student": student.pk, "teacher": teacher.pk, "service": service.pk, "location": self.location.pk,
                "room": self.rooms["Piano Room"].pk, "first_date": str(self.first), "last_date": str(self.first+timedelta(days=21)),
                "start_time": "15:00", "interval_weeks": "1"}
            form = SeriesForm(data, profile=self.profile)
            if not form.is_valid():
                raise CommandError(str(form.errors))
            rows = lessons.plan_series(form.cleaned_data)
            token = lessons.preview_token(self.user, self.profile, data, form.cleaned_data, rows)
            lessons.create_series(self.user, SLUG, data, token)
        saved(LessonBookingOffer, profile=self.profile, title="Piano trial and extra lessons", defaults={
            "service": service, "teacher": teacher, "location": self.location, "room": self.rooms["Piano Room"],
            "published": True, "public_trials": True, "family_booking": True, "makeup_eligible": True, "fee_cents": 3000})
