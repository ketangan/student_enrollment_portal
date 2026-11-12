"""Fail closed before starting an isolated demo against the wrong database."""
from time import sleep

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import connection, OperationalError

from core.models import School
from core.payments.models import PaymentAccount, PaymentOrder
from core.services.portal_environment import deployment_environment, validate_deployment_stripe_key


class Command(BaseCommand):
    help = "Verify the portal deployment boundary before starting web or scheduled workers."

    def handle(self, *args, **options):
        environment = deployment_environment()
        if environment == "demo":
            if (settings.STRIPE_MODE != "test" or settings.PAYMENTS_SQUARE_LIVE_ENABLED
                    or settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED
                    or settings.PAYMENTS_SQUARE_LIVE_APPLICATION_SECRET):
                raise CommandError("The demo deployment must use sandbox payments only.")
            self.connect_database()
            if School.objects.filter(is_demo=False).exists():
                raise CommandError("Non-demo schools found: verify the database before starting this deployment.")
            if (PaymentAccount.objects.exclude(environment="test").exists()
                    or PaymentOrder.objects.exclude(environment="test").exists()):
                raise CommandError("Non-sandbox payment evidence found in the demo database.")
            for secret, public in School.objects.values_list("app_fee_stripe_secret_key", "app_fee_stripe_public_key").iterator():
                try:
                    validate_deployment_stripe_key(secret)
                    validate_deployment_stripe_key(public, publishable=True)
                except ValidationError as exc:
                    raise CommandError("Live application-fee or unrecognized credentials found in the demo database.") from exc
        self.stdout.write(f"Portal deployment verified: {environment or 'shared local configuration'}.")

    def connect_database(self):
        # Retry only the initial connection, never maintenance work or a failed query.
        original_options = connection.settings_dict["OPTIONS"]
        if connection.vendor == "postgresql":
            connection.settings_dict["OPTIONS"] = {**original_options, "connect_timeout": 10}
        try:
            for attempt in range(1, 4):
                try:
                    connection.ensure_connection()
                    return
                except OperationalError as exc:
                    connection.close()
                    code = getattr(exc.__cause__, "pgcode", None)
                    if attempt == 3 or (code and not (code.startswith("08") or code == "57P03")):
                        raise
                    self.stderr.write(f"Database connection unavailable; retrying deployment check ({attempt}/3).")
                    sleep(5)
        finally:
            connection.settings_dict["OPTIONS"] = original_options
