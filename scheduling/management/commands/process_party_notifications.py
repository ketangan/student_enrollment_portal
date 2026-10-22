from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils import timezone

from scheduling.models import SchedulingProfile
from scheduling.party_notifications import capture_for_reservation, process, live_enabled


class Command(BaseCommand):
    help = "Capture party confirmations/reminders and process the existing outbox. Live delivery is explicitly gated."

    def add_arguments(self, parser):
        scope = parser.add_mutually_exclusive_group(required=True)
        scope.add_argument("--school")
        scope.add_argument("--all-schools", action="store_true")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 20:
            raise CommandError("Limit must be between 1 and 20.")
        profiles = SchedulingProfile.objects.select_related("school").filter(
            enabled=True, party_notifications_enabled=True)
        if options["all_schools"]:
            if settings.DEBUG or not settings.BOOKINGS_PARTY_EMAIL_ENABLED:
                self.stdout.write("Party email delivery is disabled; no schools processed.")
                return
            profiles = profiles.filter(school__is_demo=False)
        else:
            profiles = profiles.filter(school__slug=options["school"])
            if not profiles.exists():
                raise CommandError("Enable party notifications for this school first.")
        count = 0
        failures = 0
        for profile in profiles.iterator(chunk_size=100):
            if options["all_schools"] and not live_enabled(profile):
                continue
            try:
                reservations = profile.reservations.filter(party_request__isnull=False,
                    state="confirmed", starts_at__gt=timezone.now())
                if options["all_schools"]:
                    reservations = reservations.filter(order__environment="live")
                for pk in reservations.values_list("pk", flat=True).iterator(chunk_size=200):
                    capture_for_reservation(pk)
                count += process(profile.pk, options["limit"])
            except Exception:
                # One school's failure must not starve the remaining schools.
                failures += 1
                self.stderr.write(f"Party email processing failed for school ID {profile.school_id}.")
        if failures:
            raise CommandError(f"{failures} school(s) failed; review the booking outbox before retrying.")
        self.stdout.write(f"Processed {count} message(s). Review the outbox for acceptance status.")
