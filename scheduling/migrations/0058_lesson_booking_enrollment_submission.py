from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0057_teacher_self_service"),
    ]

    operations = [
        migrations.AddField(
            model_name="lessonbooking",
            name="enrollment_submission",
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lesson_bookings", to="core.submission"),
        ),
    ]
