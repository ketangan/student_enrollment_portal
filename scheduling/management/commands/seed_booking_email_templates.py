"""Explicit, additive setup of editable templates for one Bookings school."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import School, SchoolEmailTemplate
from scheduling.models import SchedulingProfile


TEMPLATES = (
    ("Confirmation", "none", "{{school_name}}: Your booking is confirmed",
     "<p>Hi {{first_name}},</p><p>Your booking is confirmed.</p><p>{{program}}<br>"
     "{{booking_date}} at {{booking_time}}<br>{{location}}<br>{{participant_count}} participants</p><p>Thank you!</p>"),
    ("Payment reminder", "payment", "{{school_name}}: Payment requested",
     "<p>Hi {{first_name}},</p><p>Please complete your reservation payment using the button below.</p><p>Thank you!</p>"),
    ("Guest details", "guests", "{{school_name}}: Your participant details",
     "<p>Hi {{first_name}},</p><p>Please add or update your participants' details and selections using the button below so we can prepare for your visit.</p><p>Thank you!</p>"),
)


class Command(BaseCommand):
    help = "Add missing standard Bookings templates to one explicitly selected school; never overwrite edits."

    def add_arguments(self, parser):
        parser.add_argument("--school", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        school = School.objects.select_for_update().filter(slug=options["school"]).first()
        if not school or not SchedulingProfile.objects.filter(school=school, enabled=True,
                native_reservations_enabled=True).exists():
            raise CommandError("Choose a school with native Bookings enabled.")
        count = 0
        for name, link, subject, body in TEMPLATES:
            if SchoolEmailTemplate.objects.filter(school=school, purpose="bookings", name=name).exists():
                continue
            SchoolEmailTemplate.objects.create(school=school, purpose="bookings", name=name,
                booking_kind="both", booking_link=link, subject=subject, body=body)
            count += 1
        self.stdout.write(f"Added {count} Bookings template(s).")
