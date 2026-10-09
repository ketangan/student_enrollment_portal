from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import School
from core.services.customer_terms import load_customer_terms, prepare_customer_terms


class Command(BaseCommand):
    help = "Preview reviewed customer trial/feature settings. Use --apply --actor to persist without launching."

    def add_arguments(self, parser):
        parser.add_argument("school_slug")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--actor", help="Active superuser username (required with --apply)")

    def handle(self, *args, **options):
        try:
            school = School.objects.get(slug=options["school_slug"])
            terms = load_customer_terms(school.slug)
            self.stdout.write(f"{school.slug}: {terms['plan']}, {terms['trial_duration_days']}-day trial at written go-live.")
            self.stdout.write(f"Feature overrides: {', '.join(sorted(terms['feature_flags']))}")
            if not options["apply"]:
                self.stdout.write("Preview only. No settings, records, access links or billing changed.")
                return
            actor = get_user_model().objects.get(username=options["actor"])
            changed = prepare_customer_terms(school_id=school.pk, actor=actor)
        except (School.DoesNotExist, get_user_model().DoesNotExist, FileNotFoundError,
                PermissionDenied, ValidationError, ValueError) as exc:
            raise CommandError(str(exc) or "An active superuser actor is required.") from exc
        self.stdout.write("Customer terms prepared. No trial started." if changed else "Already prepared; no changes.")
