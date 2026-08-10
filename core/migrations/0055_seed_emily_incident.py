from django.db import migrations
from django.utils import timezone
import datetime


def seed_emily_incident(apps, schema_editor):
    OpsIncident = apps.get_model("core", "OpsIncident")
    School = apps.get_model("core", "School")
    User = apps.get_model("auth", "User")

    # Skip if already seeded
    if OpsIncident.objects.filter(title__startswith="SBMC — Emily login failure").exists():
        return

    school = School.objects.filter(slug="sbmc").first()
    affected_user = User.objects.filter(username="sbmc_admin").first()
    admin_user = User.objects.filter(is_superuser=True).order_by("date_joined").first()

    OpsIncident.objects.create(
        title="SBMC — Emily login failure",
        occurred_at=datetime.datetime(2026, 7, 23, 0, 0, 0, tzinfo=datetime.timezone.utc),
        status="resolved",
        severity="high",
        affected_school=school,
        affected_user=affected_user,
        symptoms=(
            "Emily (info@sbmusicconservatory.com, username: sbmc_admin) reported being unable to log in. "
            "She stated she had attempted a password reset twice but it did not work. "
            "Her last successful login was July 23, 2026 (confirmed via ops portal user detail)."
        ),
        root_cause=(
            "Her Django session (2-week default TTL) expired around August 6, coinciding with a Render "
            "deploy. After the session expired she could not log back in because Safari autofill was "
            "replaying stale/incorrect credentials after the password reset — she was not typing the "
            "new password manually. The auth backend itself was confirmed working via Render shell "
            "authenticate() test. The password reset token was valid but autofill silently submitted "
            "the old password on the login form."
        ),
        resolution=(
            "Password reset directly from ops portal (/ops/users/<id>/reset-password/). "
            "Emily instructed to close Safari autofill prompt and type the new password manually: SbmcAdmin@123. "
            "She confirmed login success after following these steps."
        ),
        prevention_notes=(
            "Login audit logging added (Ph 25): every login attempt (success + failure) now logged to "
            "AdminAuditLog with IP and username_attempted field. Login History card visible on ops user "
            "detail page. EmailOrUsernameBackend deployed so users can log in with email as well as username, "
            "reducing credential confusion. Future diagnosis: check ops user detail login history before "
            "assuming backend failure."
        ),
        resolved_at=datetime.datetime(2026, 8, 10, 0, 0, 0, tzinfo=datetime.timezone.utc),
        created_by=admin_user,
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_ops_incident"),
    ]

    operations = [
        migrations.RunPython(seed_emily_incident, noop),
    ]
