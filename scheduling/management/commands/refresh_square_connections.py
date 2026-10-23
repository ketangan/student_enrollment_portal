from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import timedelta

from scheduling.integrations.square_connection import due, maintain
from scheduling.models import SquareAuthorization


class Command(BaseCommand):
    help = "Renew due Square authorizations and retry pending disconnects; never creates a payment."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--school")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 100:
            raise CommandError("Limit must be between 1 and 100.")
        checked = failed = 0
        for pk in due(options["limit"], options["school"]):
            checked += 1
            try:
                maintain(pk)
            except ValidationError:
                failed += 1
        SquareAuthorization.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1)).delete()
        self.stdout.write(f"Checked {checked} Square connections; {failed} need attention.")
        if failed:
            raise CommandError("Some Square connections need attention. Review Integrations.")
