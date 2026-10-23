"""Run the existing bounded booking jobs without one failure starving the others."""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Process party messages, renew Square connections and reconcile booking payments."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--include-live-recovery", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 20:
            raise CommandError("Limit must be between 1 and 20.")
        jobs = [
            ("process_party_notifications", {"all_schools": True}),
            ("refresh_square_connections", {}),
            ("reconcile_booking_payments", {"environment": "test", "provider": "square"}),
        ]
        if options["include_live_recovery"]:
            jobs.append(("reconcile_booking_payments", {"environment": "live", "provider": "square"}))
        failures = 0
        for name, scope in jobs:
            try:
                call_command(name, limit=options["limit"], stdout=self.stdout, stderr=self.stderr, **scope)
            except Exception:
                # Never include provider errors or credentials in scheduled-job logs.
                failures += 1
                self.stderr.write(f"{name} failed; review the corresponding booking status.")
        if failures:
            raise CommandError(f"{failures} booking maintenance task(s) failed.")
        self.stdout.write("Booking maintenance completed.")
