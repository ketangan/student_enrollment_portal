from django.core.management.base import BaseCommand, CommandError

from core.payments import recovery, refunds
from core.payments.models import PaymentAttempt, Refund
from scheduling.checkout import recover_attempt


class Command(BaseCommand):
    help = "Bounded booking payment recovery. Defaults to test; live Square requires explicit selection."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--school", help="Limit manual recovery to one business slug.")
        parser.add_argument("--environment", choices=["test", "live"], default="test")

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 100:
            raise CommandError("Limit must be between 1 and 100.")
        checked = succeeded = 0
        # Split the budget so a busy checkout queue cannot starve refunds.
        for model, active, prefix, operation in [
            (Refund, Refund.ACTIVE, "attempt__order", refunds.process),
            (PaymentAttempt, PaymentAttempt.ACTIVE, "order", recover_attempt),
        ]:
            scope = {f"{prefix}__purpose": "booking", f"{prefix}__environment": options["environment"]}
            if options["environment"] == "live":
                scope.update({f"{prefix}__account__provider": "square", f"{prefix}__account__scope": "connected"})
            if options["school"]:
                scope[f"{prefix}__school__slug"] = options["school"]
            for record in recovery.due_records(model, active=active).filter(**scope)[:max(1, limit // 2)]:
                if checked >= limit:
                    break
                succeeded += bool(operation(record))
                checked += 1
        self.stdout.write(f"Checked {checked}; verified {succeeded}. Environment: {options['environment']}.")
