"""Add a small rehearsal without resetting existing demo records."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from scheduling import inventory, preparation, reservations, requests
from scheduling.experience_demo import config, service
from scheduling.models import Offering, SchedulingProfile, Session, WorkshopRequest, RequestParticipant


class Command(BaseCommand):
    help = "Add the local Pasadena Cozy Creations workshop and three fictional bookings."

    @transaction.atomic
    def handle(self, *args, **options):
        if not config.local_database():
            raise CommandError("Only the isolated local preview database is allowed.")
        profile = SchedulingProfile.objects.select_for_update().get(school__slug="plushumi-demo")
        if not config.available(profile) or profile.school.stripe_customer_id or profile.school.smtp_host or profile.school.app_fee_stripe_secret_key:
            raise CommandError("Refusing a connected or non-demo school.")
        settings = config.configuration(profile.school.slug)
        user = service.actor(profile, settings)
        location = profile.locations.get(name="Pasadena, CA")
        original = Offering.objects.get(location=location, name="Plushie-making workshop")
        workshop, created = Offering.objects.get_or_create(location=location, name="Cozy Creations",
            defaults={"inventory_catalog": original.inventory_catalog, "is_published": True})
        if not created:
            self.stdout.write("Cozy Creations already exists; existing sessions and bookings preserved.")
            return
        day = timezone.localdate() + timedelta(days=1)
        while day.weekday() != 6:
            day += timedelta(days=1)
        room = location.rooms.get(name="Workshop studio")
        groups = requests.catalog_for(workshop)
        variants = inventory.availability_payload(workshop)["variants"]
        if not variants:
            raise CommandError("The existing catalog needs bookable material recipes first.")
        for index, (parent, children) in enumerate([
            ("Morgan Ellis", ["Riley", "Avery"]), ("Taylor Brooks", ["Jamie", "Casey", "Quinn"]),
            ("Cameron Lee", ["Skyler"]),
        ]):
            start = datetime.combine(day, time(10 + index * 2), ZoneInfo(location.timezone_name))
            session = Session(offering=workshop, room=room, starts_at=start,
                ends_at=start + timedelta(minutes=90), capacity=12, is_published=True)
            session.full_clean()
            session.save()
            source = WorkshopRequest.objects.create(session=session, purchaser_name=parent,
                purchaser_email=f"cozy-demo-{index}@example.invalid", submission_key=uuid.uuid4(),
                submission_digest="local-demo", browser_digest="local-demo",
                session_snapshot={"workshop": workshop.name, "location": location.name,
                    "timezone": location.timezone_name, "starts_at": start.isoformat(),
                    "ends_at": session.ends_at.isoformat(), "experience_demo": {"policy": settings["policy_revision"]}})
            for position, name in enumerate(children):
                selections = []
                variant = variants[(index + position) % len(variants)]
                for group in groups:
                    option = next((choice for choice in group.active_options if choice.pk in variant["options"]),
                        next((choice for choice in group.active_options if choice.label == "8"), group.active_options[0]))
                    selections.append({"group_id": group.pk, "group": group.name,
                        "option_id": option.pk, "option": option.label,
                        "swatch": option.swatch, "image_url": option.image_url})
                RequestParticipant.objects.create(request=source, name=name, position=position, selections=selections)
            booking = reservations.create_reservation(user, profile.school.slug, "workshop", source.public_id,
                total_cents=len(children) * settings["price_cents"], deposit_cents=0, online=False)
            service.simulate(profile, source, settings, "complete")
            if index == 1:
                booking.refresh_from_db()
                form = preparation.form_for(booking, profile)
                preparation.update(user, profile.school.slug, booking.public_id,
                    {"action": "prepare", "edit_revision": form["edit_revision"].value()})
        self.stdout.write(f"Cozy Creations: {day:%b %d, %Y}, three sessions and three confirmed bookings (six participants).")
