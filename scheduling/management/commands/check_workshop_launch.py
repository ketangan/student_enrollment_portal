from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from scheduling.models import SchedulingProfile
from scheduling.workshop_experience import available, live_account


class Command(BaseCommand):
    help = "Read-only preflight for a real school's customer workshop flow. Does not activate bookings or charge cards."

    def add_arguments(self, parser):
        parser.add_argument("school")

    def handle(self, school, **options):
        profile = SchedulingProfile.objects.select_related("school").filter(school__slug=school).first()
        if not profile or profile.school.is_demo:
            raise CommandError("Choose an existing non-demo scheduling school.")
        errors = []
        if not available(profile, require_intake=True):
            errors.append("Enable the school customer experience, workshops/reservations capabilities and native intake.")
        from scheduling.experience_demo.service import sessions
        from scheduling.requests import catalog_for, accepts_reservations
        from scheduling.reservations import workshop_quote, workshop_unit_price
        upcoming = list(sessions(profile, online=True))
        if not upcoming:
            errors.append("No upcoming published, priced workshop sessions.")
        checked_locations = set()
        for session in upcoming:
            offering = session.offering
            if offering.location_id not in checked_locations:
                checked_locations.add(offering.location_id)
                try:
                    live_account(profile, offering.location)
                except ValidationError as exc:
                    errors.append(f"{offering.location.name}: {' '.join(exc.messages)}")
            if not accepts_reservations(session, catalog_for(offering)):
                errors.append(f"{offering.name}: session {session.pk} has unavailable catalog choices.")
            try:
                workshop_quote(offering, workshop_unit_price(offering), 1)
            except ValidationError as exc:
                errors.append(f"{offering.name}: {' '.join(exc.messages)}")
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(self.style.SUCCESS(
            f"Configuration checks passed for {school}: {len(upcoming)} sessions, {len(checked_locations)} locations. "
            "Physical-device, live payment/refund, inbox delivery and policy acceptance are still required."))
