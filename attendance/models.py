"""
SmartAttendance data model (3NF).

Every non-key attribute depends only on its table's primary key:
  * Identity/auth data lives on ``User``.
  * Address and student-specific data are split into 1:1 tables.
  * Subject ↔ Student many-to-many is resolved by ``SubjectEnrollment``.
  * Attendance facts reference a ``ClassSession`` (which carries the subject),
    never duplicating subject/teacher columns.

NOTE: There are intentionally NO tuition, billing, fee or payment tables.
"""
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class SmartUserManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.SUPERADMIN)
        extra_fields.setdefault("status", User.AccountStatus.ACTIVE)
        return super().create_superuser(username, email, password, **extra_fields)

    def students(self):
        return self.filter(role=User.Role.STUDENT)

    def instructors(self):
        return self.filter(role=User.Role.INSTRUCTOR)


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPERADMIN = "admin", "Superadmin"
        INSTRUCTOR = "instructor", "Instructor"
        STUDENT = "student", "Student"

    class AccountStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.STUDENT, db_index=True)
    status = models.CharField(
        max_length=20, choices=AccountStatus.choices, default=AccountStatus.PENDING, db_index=True
    )
    # Browser/device UUID used for anti-proxy hardware binding. NULL = unbound.
    # One phone per student: a bound device ID may belong to only one account (see Meta.constraints).
    device_id = models.CharField(max_length=255, null=True, blank=True)
    date_joined = models.DateTimeField(auto_now_add=True)

    objects = SmartUserManager()

    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["device_id"],
                condition=models.Q(device_id__isnull=False) & ~models.Q(device_id=""),
                name="unique_bound_device",
                violation_error_message="This phone is already bound to another student account.",
            ),
        ]

    def __str__(self):
        return self.get_full_name() or self.username

    # -- role helpers ------------------------------------------------------
    @property
    def is_superadmin(self):
        return self.role == self.Role.SUPERADMIN

    @property
    def is_instructor(self):
        return self.role == self.Role.INSTRUCTOR

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT

    # -- status helpers ----------------------------------------------------
    @property
    def is_pending(self):
        return self.status == self.AccountStatus.PENDING

    @property
    def is_suspended(self):
        return self.status == self.AccountStatus.SUSPENDED

    @property
    def is_archived(self):
        """Soft-deleted accounts are kept for audit history but cannot log in."""
        return not self.is_active

    @property
    def display_status(self):
        return "Archived" if self.is_archived else self.get_status_display()

    @property
    def id_number(self):
        profile = getattr(self, "student_profile", None)
        return profile.student_id_number if profile else ""


class Address(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="address")
    street_address = models.CharField(max_length=255, blank=True)
    barangay = models.CharField(max_length=100, blank=True)
    city_municipality = models.CharField("city / municipality", max_length=100)
    province = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=10, blank=True)

    class Meta:
        verbose_name_plural = "addresses"

    def __str__(self):
        parts = [self.street_address, self.barangay and f"Brgy. {self.barangay}",
                 self.city_municipality, self.province, self.postal_code]
        return ", ".join(p for p in parts if p)


class StudentProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    student_id_number = models.CharField(max_length=50, unique=True)
    course_program = models.CharField(max_length=150)
    year_level = models.PositiveSmallIntegerField(default=1)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_number = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["student_id_number"]

    def __str__(self):
        return f"{self.student_id_number} – {self.user}"


# ---------------------------------------------------------------------------
# Academic structure
# ---------------------------------------------------------------------------
class Department(models.Model):
    """Academic department used by the Superadmin to group subject allocations."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} – {self.name}"


class Subject(models.Model):
    subject_code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    teacher = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="assigned_subjects",
        limit_choices_to={"role": "instructor"},
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="subjects"
    )

    class Meta:
        ordering = ["subject_code"]

    def __str__(self):
        return f"{self.subject_code} – {self.title}"


class SubjectEnrollment(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="enrolled_subjects",
        limit_choices_to={"role": "student"},
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["subject", "student__last_name"]
        constraints = [
            models.UniqueConstraint(fields=["subject", "student"], name="unique_subject_student_enrollment"),
        ]

    def __str__(self):
        return f"{self.student} → {self.subject.subject_code}"


# ---------------------------------------------------------------------------
# Sessions & attendance
# ---------------------------------------------------------------------------
class ClassSession(models.Model):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="sessions")
    session_title = models.CharField(max_length=150, blank=True)
    date = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    instructor_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    instructor_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    allowed_radius_meters = models.PositiveIntegerField(default=40)
    # Scans more than this many minutes after the session started are marked late (chosen per session).
    late_after_minutes = models.PositiveSmallIntegerField(default=15)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject.subject_code} · {self.session_title or self.date}"

    @property
    def is_open(self):
        return self.is_active and timezone.now() <= self.expires_at

    @property
    def has_geofence(self):
        return self.instructor_latitude is not None and self.instructor_longitude is not None


class AttendanceLog(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        ABSENT = "absent", "Absent"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="attendance_logs")
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        limit_choices_to={"role": "student"},
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PRESENT)
    scanned_at = models.DateTimeField(auto_now_add=True)
    scanned_device_id = models.CharField(max_length=255)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-scanned_at"]
        constraints = [
            models.UniqueConstraint(fields=["session", "student"], name="unique_session_student_attendance"),
        ]

    def __str__(self):
        return f"{self.student} · {self.session} · {self.status}"


class AuditScanLog(models.Model):
    class Reason(models.TextChoices):
        UNENROLLED_STUDENT = "unenrolled_student", "Unenrolled student"
        DEVICE_MISMATCH = "device_mismatch", "Device mismatch"
        OUT_OF_BOUNDS = "out_of_bounds", "Out of bounds"
        EXPIRED_TOKEN = "expired_token", "Expired / invalid token"
        SESSION_CLOSED = "session_closed", "Session closed"

    session = models.ForeignKey(
        ClassSession, on_delete=models.CASCADE, null=True, blank=True, related_name="audit_logs"
    )
    student = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_scan_logs"
    )
    reason = models.CharField(max_length=30, choices=Reason.choices, db_index=True)
    student_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    student_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    submitted_device_id = models.CharField(max_length=255, null=True, blank=True)
    raw_payload = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_reason_display()} · {self.student} · {self.created_at:%Y-%m-%d %H:%M}"
