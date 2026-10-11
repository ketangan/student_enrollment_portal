from django.core.management.base import BaseCommand, CommandError

from core.payments import recovery, refunds
from core.payments.models import PaymentAttempt, Refund
from scheduling.checkout import recover_attempt


class Command(BaseCommand):
    help = "Bounded test-only booking recovery. Schedule externally before any paid pilot."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 100:
            raise CommandError("Limit must be between 1 and 100.")
        checked = succeeded = 0
        # Split the budget so a busy checkout queue cannot starve refunds.
        for model, active, scope, operation in [
            (Refund, Refund.ACTIVE, {"attempt__order__purpose": "booking"}, refunds.process),
            (PaymentAttempt, PaymentAttempt.ACTIVE, {"order__purpose": "booking"}, recover_attempt),
        ]:
            for record in recovery.due_records(model, active=active).filter(**scope)[:max(1, limit // 2)]:
                if checked >= limit:
                    break
                succeeded += bool(operation(record))
                checked += 1
        self.stdout.write(f"Checked {checked}; verified {succeeded}. No live payment support enabled.")
