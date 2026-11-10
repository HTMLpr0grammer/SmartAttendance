from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance"
    verbose_name = "SmartAttendance"

    def ready(self):
        from . import signals  # noqa: F401
