"""Fail closed before starting an isolated demo against the wrong database."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

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
