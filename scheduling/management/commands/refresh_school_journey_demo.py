"""Refresh fictional local school examples without altering existing bookings."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import SchoolAdminMembership, SchoolProgram
from scheduling.models import (Camp, LessonTeacher, Location, Offering,
    Room, SchedulingMembership, SchedulingProfile, Session)
from .seed_integrated_booking_demo import PREVIEW_DATABASE, SLUG


class Command(BaseCommand):
    help = "Add current class/program examples and teacher access to the existing local school demo."

    @transaction.atomic
    def handle(self, *args, **options):
        database = settings.DATABASES["default"]
        if (not settings.DEBUG or database["NAME"] != PREVIEW_DATABASE
                or database.get("HOST") not in {"localhost", "127.0.0.1"}
                or settings.EMAIL_BACKEND != "django.core.mail.backends.locmem.EmailBackend"):
            raise CommandError("Only the existing local preview with in-memory email is allowed.")
        profile = SchedulingProfile.objects.select_related("school").get(school__slug=SLUG)
        if not profile.school.is_demo or profile.school.smtp_host or profile.school.stripe_customer_id:
            raise CommandError("Refusing to change a non-demo or externally connected school.")
        location = Location.objects.get(profile=profile, name="Main Campus")
        zone = ZoneInfo(location.timezone_name)
        first = timezone.localdate(timezone=zone) + timedelta(days=7)
        first += timedelta(days=(7 - first.weekday()) % 7)
        room = Room.objects.get(location=location, name="Creative Studio")
        workshop, _ = Offering.objects.get_or_create(location=location, name="Family music workshop",
            defaults={"description": "A shared music class for children and their grown-ups.",
                "is_published": True, "accepts_requests": True, "price_per_participant": 20})
        seasonal, _ = Offering.objects.get_or_create(location=location, name="Seasonal discovery program",
            defaults={"description": "Three mornings of music and creative learning.", "is_published": True})

        def session(offering, day, hour, minutes):
            start = datetime.combine(day, time(hour), zone)
            item, created = Session.objects.get_or_create(offering=offering, starts_at=start,
                defaults={"ends_at": start + timedelta(minutes=minutes), "room": room,
                    "capacity": 10, "is_published": True})
            if created:
                item.full_clean()
            return item

        for days in (1, 8):
            session(workshop, first + timedelta(days=days), 16, 60)
        dates = [session(seasonal, first + timedelta(days=day), 9, 180) for day in (0, 1, 2)]
        camp, created = Camp.objects.get_or_create(offering=seasonal, name=f"Discovery program - {first:%b %Y}",
            defaults={"price": 150, "deposit": 0, "online": False, "is_open": True})
        if created:
            camp.sessions.set(dates)
            camp.full_clean()
        SchoolProgram.objects.get_or_create(school=profile.school, code="creative", defaults={"name": "Creative Learning"})
        SchoolProgram.objects.get_or_create(school=profile.school, code="music", defaults={"name": "Music Foundations"})
        for teacher in LessonTeacher.objects.filter(profile=profile, is_active=True):
            username = f"school_teacher_{teacher.pk}"
            user, created = get_user_model().objects.get_or_create(username=username,
                defaults={"first_name": teacher.name, "is_active": True})
            if (user.is_staff or user.is_superuser or SchoolAdminMembership.objects.filter(user=user).exists()
                    or SchedulingMembership.objects.filter(user=user).exists()
                    or LessonTeacher.objects.filter(login_user=user).exclude(pk=teacher.pk).exists()):
                raise CommandError("Refusing to modify a shared teacher account.")
            if created:
                user.set_password("admin")
                user.save(update_fields=["password"])
            if teacher.login_user_id not in (None, user.pk):
                raise CommandError("Teacher already has a different login; keeping it unchanged.")
            teacher.login_user = user
            teacher.save(update_fields=["login_user"])
            self.stdout.write(f"{teacher.name}: {username}")
        self.stdout.write(self.style.SUCCESS(f"Current examples start {first}; existing bookings unchanged."))
