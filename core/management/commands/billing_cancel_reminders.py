# core/management/commands/billing_cancel_reminders.py
"""
Management command to check for upcoming and overdue subscription cancellations.

Logs warnings (3-day advance notice) and errors (overdue) to help operators
proactively manage billing lifecycle. Logs appear in Sentry for monitoring.

Usage:
    python manage.py billing_cancel_reminders
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.models import School

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check for schools with upcoming or overdue subscription cancellations (logs to Sentry)"

    def handle(self, *args, **options):
        now = timezone.now()
        warning_cutoff = now + timedelta(days=3)

        # Find schools with cancellations scheduled within 3 days
        upcoming_schools = School.objects.filter(
            is_active=True,
        ).filter(
            Q(stripe_cancel_at__isnull=False, stripe_cancel_at__lte=warning_cutoff, stripe_cancel_at__gt=now)
            | Q(
                stripe_cancel_at_period_end=True,
                stripe_current_period_end__isnull=False,
                stripe_current_period_end__lte=warning_cutoff,
                stripe_current_period_end__gt=now,
            )
        ).select_related()

        # Find schools with overdue cancellations (still active but should be locked)
        overdue_schools = School.objects.filter(
            is_active=True,
        ).filter(
            Q(stripe_cancel_at__isnull=False, stripe_cancel_at__lte=now)
            | Q(
                stripe_cancel_at_period_end=True,
                stripe_current_period_end__isnull=False,
                stripe_current_period_end__lte=now,
            )
        ).select_related()

        # Log upcoming cancellations as WARNING
        for school in upcoming_schools:
            end_date = school.stripe_cancel_at or school.stripe_current_period_end
            logger.warning(
                "Billing: school '%s' (slug=%s) subscription will cancel on %s",
                school.display_name or school.slug,
                school.slug,
                end_date.strftime("%Y-%m-%d %H:%M:%S %Z"),
            )

        # Log overdue cancellations as ERROR
        for school in overdue_schools:
            end_date = school.stripe_cancel_at or school.stripe_current_period_end
            logger.error(
                "Billing: school '%s' (slug=%s) subscription ENDED on %s but is_active=True (manual deactivation needed)",
                school.display_name or school.slug,
                school.slug,
                end_date.strftime("%Y-%m-%d %H:%M:%S %Z"),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Checked billing cancellations: {upcoming_schools.count()} upcoming, {overdue_schools.count()} overdue"
            )
        )
