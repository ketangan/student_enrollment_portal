from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_owners(apps, schema_editor):
    """All pre-existing memberships become owners."""
    SchoolAdminMembership = apps.get_model("core", "SchoolAdminMembership")
    SchoolAdminMembership.objects.filter(role="").update(role="owner")
    SchoolAdminMembership.objects.filter(is_active=None).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_school_default_follow_up_days"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Add new columns with temporary nullability so existing rows don't violate constraints.
        migrations.AddField(
            model_name="schooladminmembership",
            name="role",
            field=models.CharField(
                max_length=10,
                choices=[("owner", "Owner"), ("editor", "Editor"), ("viewer", "Viewer")],
                default="owner",
            ),
        ),
        migrations.AddField(
            model_name="schooladminmembership",
            name="is_active",
            field=models.BooleanField(default=True, db_index=True),
        ),
        migrations.AddField(
            model_name="schooladminmembership",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name="schooladminmembership",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="created_memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 2. Backfill: all existing rows become owners.
        migrations.RunPython(backfill_owners, migrations.RunPython.noop),
        # 3. Swap OneToOneField → ForeignKey.
        migrations.AlterField(
            model_name="schooladminmembership",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="school_memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 4. Add uniqueness constraint (school, user).
        migrations.AddConstraint(
            model_name="schooladminmembership",
            constraint=models.UniqueConstraint(
                fields=["school", "user"],
                name="unique_school_admin_membership",
            ),
        ),
    ]
