from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.http import Http404

from scheduling.messaging import process_batch
from scheduling.models import SchedulingProfile


class Command(BaseCommand):
    help = "Process a bounded local-only Bookings message batch; never sends externally."

    def add_arguments(self, parser):
        parser.add_argument("--school", required=True)
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        try:
            profile = SchedulingProfile.objects.get(school__slug=options["school"])
            count = process_batch(profile.pk, limit=options["limit"])
        except (SchedulingProfile.DoesNotExist, Http404, ValidationError) as exc:
            raise CommandError("Local preview, workspace and valid batch limit are required.") from exc
        self.stdout.write(f"Processed {count} local message(s); no external email sent.")
