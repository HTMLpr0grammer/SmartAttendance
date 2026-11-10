from rest_framework import serializers

from .models import AttendanceLog


class ScanSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=2048)
    latitude = serializers.FloatField(required=False, allow_null=True, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, allow_null=True, min_value=-180, max_value=180)
    device_id = serializers.CharField(max_length=255)


class StartSessionSerializer(serializers.Serializer):
    subject_id = serializers.IntegerField()
    session_title = serializers.CharField(max_length=150, required=False, allow_blank=True)
    duration_minutes = serializers.IntegerField(min_value=5, max_value=360, default=60)
    allowed_radius_meters = serializers.IntegerField(min_value=10, max_value=1000, default=40)
    latitude = serializers.FloatField(required=False, allow_null=True, min_value=-90, max_value=90)
    longitude = serializers.FloatField(required=False, allow_null=True, min_value=-180, max_value=180)
    # Per-session switch. ON (default) = students must be inside the radius; OFF = location is not
    # checked at all (e.g. online / remote classes). Stored as "no coordinates" – no schema change.
    geofence_enabled = serializers.BooleanField(default=True)
    # Minutes after the start when a scan becomes "late" (10, 15, 30 or a custom value).
    late_after_minutes = serializers.IntegerField(min_value=1, max_value=180, required=False)

    def validate(self, attrs):
        from django.conf import settings
        late = attrs.setdefault("late_after_minutes", settings.SMARTATTENDANCE["LATE_THRESHOLD_MINUTES"])
        if late >= attrs.get("duration_minutes", 60):
            raise serializers.ValidationError(
                "The late time must be shorter than the session duration, otherwise nobody could be marked late."
            )
        if attrs.get("geofence_enabled", True) and (attrs.get("latitude") is None or attrs.get("longitude") is None):
            raise serializers.ValidationError(
                "Your location is required when geofencing is on. Allow GPS access, "
                "or turn geofencing off for this session."
            )
        return attrs


class AttendanceUpdateSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=AttendanceLog.Status.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate(self, attrs):
        if "status" not in attrs and "notes" not in attrs:
            raise serializers.ValidationError("Provide a status and/or notes.")
        return attrs


class EnrollSerializer(serializers.Serializer):
    MAX_BULK = 200

    identifier = serializers.CharField(max_length=150, required=False, allow_blank=True,
                                       help_text="Student ID number, username or e-mail")
    identifiers = serializers.CharField(max_length=20000, required=False, allow_blank=True,
                                        help_text="Several IDs, usernames or e-mails separated by commas, "
                                                  "spaces or new lines (bulk enrollment)")

    def validate(self, attrs):
        from .directory import split_identifiers

        single = (attrs.get("identifier") or "").strip()
        many = split_identifiers(attrs.get("identifiers"))
        if not single and not many:
            raise serializers.ValidationError("Enter a student ID number, username or e-mail.")
        if len(many) > self.MAX_BULK:
            raise serializers.ValidationError(f"Enroll at most {self.MAX_BULK} students at a time.")
        attrs["identifier"], attrs["identifier_list"] = single, many
        return attrs
