# One phone per student: release phones shared by several accounts, then enforce uniqueness.

from django.db import migrations, models
from django.db.models import Max, Q


def release_shared_phones(apps, schema_editor):
    """Before the constraint is added, make sure no phone is bound to two accounts.

    Empty strings become NULL. When several accounts share one device ID, the account that most recently
    checked in with that phone keeps it (the earliest account if none did); the others are unbound and
    their next scan binds their own phone.
    """
    User = apps.get_model("attendance", "User")
    User.objects.filter(device_id="").update(device_id=None)
    shared = (User.objects.exclude(device_id=None).order_by().values("device_id")
              .annotate(n=models.Count("id")).filter(n__gt=1).values_list("device_id", flat=True))
    for device in list(shared):
        holders = (User.objects.filter(device_id=device)
                   .annotate(last_scan=Max("attendance_records__scanned_at",
                                           filter=Q(attendance_records__scanned_device_id=device)))
                   .order_by(models.F("last_scan").desc(nulls_last=True), "pk"))
        keep = holders.first()
        User.objects.filter(device_id=device).exclude(pk=keep.pk).update(device_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(release_shared_phones, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("device_id__isnull", False),
                    models.Q(("device_id", ""), _negated=True),
                ),
                fields=("device_id",),
                name="unique_bound_device",
                violation_error_message="This phone is already bound to another student account.",
            ),
        ),
    ]
