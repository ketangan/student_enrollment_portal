from django.db import migrations


def preserve_assignments(apps, schema_editor):
    Session = apps.get_model("scheduling", "Session")
    Reservation = apps.get_model("scheduling", "Reservation")
    alias = schema_editor.connection.alias
    for session in Session.objects.using(alias).exclude(staff_names="").iterator():
        Reservation.objects.using(alias).filter(
            session_id=session.pk, staff_names=""
        ).update(staff_names=session.staff_names)


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0049_preparation_staff_names")]

    operations = [
        migrations.RunPython(preserve_assignments, migrations.RunPython.noop),
        migrations.RemoveField(model_name="session", name="staff_names"),
    ]
