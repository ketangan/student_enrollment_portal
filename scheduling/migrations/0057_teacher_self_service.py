from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0056_squareterminal_ping_checked_at_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonteacher", name="login_user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="lesson_teacher_accounts", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name="lessonteacher",
            constraint=models.UniqueConstraint(fields=("profile", "login_user"), name="lesson_teacher_login_unique"),
        ),
        migrations.AddField(model_name="lessonavailability", name="effective_from",
            field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="lessonavailability", name="effective_until",
            field=models.DateField(blank=True, null=True)),
        migrations.AddConstraint(
            model_name="lessonavailability",
            constraint=models.CheckConstraint(condition=models.Q(effective_from__isnull=True)
                | models.Q(effective_until__isnull=True) | models.Q(effective_until__gte=models.F("effective_from")),
                name="lesson_availability_dates_valid"),
        ),
    ]
