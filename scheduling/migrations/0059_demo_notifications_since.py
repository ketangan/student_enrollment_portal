from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0058_lesson_booking_enrollment_submission")]
    operations = [migrations.AddField(
        model_name="schedulingprofile", name="demo_notifications_since",
        field=models.DateTimeField(default=timezone.now, editable=False),
    )]
