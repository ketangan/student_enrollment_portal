from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import PaymentNotification
from core.services.payment_notifications import deliver_payment_notice


class Command(BaseCommand):
    help = "Retry queued payment emails. Run every minute; never charges a card."

    def handle(self, *args, **options):
        ids = list(PaymentNotification.objects.filter(
            sent_at__isnull=True, next_attempt_at__lte=timezone.now()
        ).order_by("next_attempt_at").values_list("pk", flat=True)[:100])
        for pk in ids:
            deliver_payment_notice(pk)
        if PaymentNotification.objects.filter(pk__in=ids, sent_at__isnull=True).exists():
            raise CommandError("Some payment emails remain unsent; check payment notification logs.")
        self.stdout.write(f"Processed {len(ids)} payment notifications.")
