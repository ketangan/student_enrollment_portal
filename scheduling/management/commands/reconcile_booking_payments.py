from django.core.management.base import BaseCommand, CommandError

from core.payments import recovery, refunds
from core.payments.models import PaymentAttempt, Refund
from scheduling.checkout import recover_attempt


class Command(BaseCommand):
    help = "Bounded test-only booking recovery. Schedule externally before any paid pilot."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--school", help="Limit manual recovery to one business slug.")

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 100:
            raise CommandError("Limit must be between 1 and 100.")
        checked = succeeded = 0
        # Split the budget so a busy checkout queue cannot starve refunds.
        for model, active, scope, operation in [
            (Refund, Refund.ACTIVE, {"attempt__order__purpose": "booking", "attempt__order__environment": "test"}, refunds.process),
            (PaymentAttempt, PaymentAttempt.ACTIVE, {"order__purpose": "booking", "order__environment": "test"}, recover_attempt),
        ]:
            if options["school"]:
                prefix = "attempt__order" if model is Refund else "order"
                scope[f"{prefix}__school__slug"] = options["school"]
            for record in recovery.due_records(model, active=active).filter(**scope)[:max(1, limit // 2)]:
                if checked >= limit:
                    break
                succeeded += bool(operation(record))
                checked += 1
        self.stdout.write(f"Checked {checked}; verified {succeeded}. No live payment support enabled.")
