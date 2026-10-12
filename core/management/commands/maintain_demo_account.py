"""Explicit, dry-run-first maintenance of a single demo-only account."""
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.services.demo_access import demo_only_account, eligible_members


class Command(BaseCommand):
    help = "Rename one demo-only account and optionally hard-delete its old audit entries. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("school_slug")
        parser.add_argument("--username", required=True)
        parser.add_argument("--rename", required=True)
        parser.add_argument("--delete-old-audit", action="store_true")
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        school = School.objects.filter(slug=options["school_slug"], is_demo=True).first()
        users = User.objects.select_for_update() if options["apply"] else User.objects.all()
        user = users.filter(username=options["username"]).first()
        if school is None or user is None or user.is_superuser or not demo_only_account(user, school):
            raise CommandError("Refusing a missing, live, privileged or cross-school account.")
        if not any(eligible_members(school, destination).filter(user=user).exists() for destination in ("enrollment", "scheduling")):
            raise CommandError("The account must be an active member of the specified demo.")
        new_name = options["rename"]
        try:
            User._meta.get_field("username").clean(new_name, user)
        except ValidationError as exc:
            raise CommandError("Invalid username.") from exc
        if User.objects.filter(username=new_name).exclude(pk=user.pk).exists():
            raise CommandError("That username belongs to another account; no changes made.")
        cutoff = timezone.now()
        audit = AdminAuditLog.objects.filter(created_at__lt=cutoff).filter(
            Q(actor=user) | Q(model_label__iexact="auth.user", object_id=str(user.pk)))
        history = LogEntry.objects.filter(action_time__lt=cutoff).filter(
            Q(user=user) | Q(content_type=ContentType.objects.get_for_model(User), object_id=str(user.pk)))
        counts = (audit.count(), history.count()) if options["delete_old_audit"] else (0, 0)
        self.stdout.write(f"Account {user.pk}: {user.username} -> {new_name}; audit rows: {counts[0]}; Django history rows: {counts[1]}.")
        if not options["apply"]:
            self.stdout.write("Dry run only. Back up the database before applying.")
            return
        if options["delete_old_audit"]:
            audit.delete()
            history.delete()
        user.username = new_name
        user.save(update_fields=["username"])
        AdminAuditLog.objects.create(action="change", model_label="auth.user", object_id=str(user.pk),
            object_repr=new_name, extra={"name": "demo_account_updated", "school_id": school.pk,
                "deleted_audit_rows": counts[0], "deleted_admin_history_rows": counts[1]})
        self.stdout.write("Applied. Password, permissions, memberships and business records were preserved.")
