"""
Seed realistic demo data for SmartAttendance.

    python manage.py seed_data                         # random password, printed once
    python manage.py seed_data --password "Your-Own-Pass#1"

Creates
  * 1 Superadmin            admin@school.edu.ph
  * 2 Instructors           1 active, 1 pending approval
  * 5 Students              varied attendance histories, incl. 1 pending student
                            and 1 student whose device is unbound
  * 2 Subjects              IS201 - Systems Analysis, CS101 - Web Development
  * Past class sessions, attendance logs and a few AuditScanLog rows so every
    dashboard (admin analytics, truancy watchlist, proxy feed) has data.

The command is idempotent and never deletes anything: running it again only
fills in what is missing (safe-data-management rule – no accidental wipes).
"""
import json
import secrets
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from attendance.models import (
    Address,
    AttendanceLog,
    AuditScanLog,
    ClassSession,
    Department,
    StudentProfile,
    Subject,
    SubjectEnrollment,
    User,
)

CONF = settings.SMARTATTENDANCE

SEED_MARKER = "seed_data"  # stored in AuditScanLog.raw_payload so re-runs don't duplicate demo alerts

# Approximate Pagadian City centre – demo geofence only. Instructors capture the
# real GPS fix from their own device when they start a session.
DEMO_LAT = Decimal("7.825700")
DEMO_LNG = Decimal("123.437000")

PRESENT, LATE, ABSENT = "present", "late", "absent"
CODE = {"P": PRESENT, "L": LATE, "A": ABSENT}

# One letter per past weekly session, oldest → newest.
HISTORY = {
    "IS201": {
        "ana": "PPPPPPPP",      # perfect
        "ben": "PLLPLLPL",      # frequent tardy
        "carla": "APAAPAAP",    # critical absences
        "diego": "PPLPAPPP",    # mostly fine
    },
    "CS101": {
        "ana": "PPPPPPLP",
        "ben": "LPLPPLPP",
        "diego": "PPAPPPPL",
    },
}

INSTRUCTORS = [
    dict(username="jdelacruz", email="jdelacruz@school.edu.ph", first_name="Juan", last_name="Dela Cruz",
         status=User.AccountStatus.ACTIVE, barangay="Balangasan", city="Pagadian City"),
    dict(username="mreyes", email="mreyes@school.edu.ph", first_name="Maria", last_name="Reyes",
         status=User.AccountStatus.PENDING, barangay="Santa Maria", city="Pagadian City"),
]

STUDENTS = [
    dict(key="ana", username="asantos", email="asantos@student.school.edu.ph", first_name="Ana", last_name="Santos",
         sid="2024-0001", course="BS Information Systems", year=2, status=User.AccountStatus.ACTIVE, bound=True,
         barangay="Gatas", city="Pagadian City"),
    dict(key="ben", username="bramirez", email="bramirez@student.school.edu.ph", first_name="Ben", last_name="Ramirez",
         sid="2024-0002", course="BS Information Systems", year=2, status=User.AccountStatus.ACTIVE, bound=True,
         barangay="San Pedro", city="Pagadian City"),
    dict(key="carla", username="cmendoza", email="cmendoza@student.school.edu.ph", first_name="Carla",
         last_name="Mendoza", sid="2024-0003", course="BS Computer Science", year=2,
         status=User.AccountStatus.ACTIVE, bound=True, barangay="Tiguma", city="Pagadian City"),
    # Unbound device: next successful scan will bind whichever phone they use.
    dict(key="diego", username="dvillanueva", email="dvillanueva@student.school.edu.ph", first_name="Diego",
         last_name="Villanueva", sid="2024-0004", course="BS Computer Science", year=3,
         status=User.AccountStatus.ACTIVE, bound=False, barangay="Lumbia", city="Pagadian City"),
    # Pending: self-registered, not yet enrolled by any instructor.
    dict(key="elena", username="egarcia", email="egarcia@student.school.edu.ph", first_name="Elena",
         last_name="Garcia", sid="2025-0005", course="BS Information Systems", year=1,
         status=User.AccountStatus.PENDING, bound=True, barangay="Kawit", city="Molave"),
]

SUBJECTS = [
    dict(code="IS201", title="Systems Analysis", students=["ana", "ben", "carla", "diego"]),
    dict(code="CS101", title="Web Development", students=["ana", "ben", "diego"]),
]


def device_for(username):
    """Deterministic RFC 4122 (v5) UUID so repeated seeding yields the same 'phone'."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"smartattendance.demo.{username}"))


class Command(BaseCommand):
    help = "Seed demo users, subjects, sessions, attendance history and audit alerts (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=None,
                            help="Password for newly created demo accounts. If omitted, a random one is "
                                 "generated and printed once at the end.")
        parser.add_argument("--weeks", type=int, default=8,
                            help="Number of past weekly sessions per subject (max 8 with the built-in patterns).")

    # ------------------------------------------------------------------ helpers
    def _user(self, *, username, email, password, role, status, first_name, last_name,
              device_id=None, **extra):
        """Create the account if missing. Existing accounts are never modified (safe re-runs)."""
        user = User.objects.filter(username=username).first() or User.objects.filter(email__iexact=email).first()
        if user is not None:
            self.stdout.write(f"  exists  {user.role:<10} {user.email}")
            return user
        user = User(username=username, email=email, first_name=first_name, last_name=last_name,
                    role=role, status=status, device_id=device_id, **extra)
        user.set_password(password)
        user.save()
        self.stdout.write(f"  created {role:<10} {user.email}")
        return user

    def _address(self, user, barangay, city, province="Zamboanga del Sur", postal="7016"):
        Address.objects.get_or_create(
            user=user,
            defaults=dict(street_address="", barangay=barangay, city_municipality=city,
                          province=province, postal_code=postal),
        )

    @staticmethod
    def _aware(day, hour, minute=0):
        return timezone.make_aware(datetime.combine(day, time(hour, minute)))

    # ------------------------------------------------------------------ main
    @transaction.atomic
    def handle(self, *args, **opts):
        # No password is stored in the code: use the one given, or generate a random one.
        password = opts["password"] or secrets.token_urlsafe(9)
        weeks = max(1, min(opts["weeks"], 8))

        self.stdout.write(self.style.MIGRATE_HEADING("Users"))
        admin = self._user(
            username="admin", email="admin@school.edu.ph", password=password,
            role=User.Role.SUPERADMIN, status=User.AccountStatus.ACTIVE,
            first_name="System", last_name="Administrator", is_staff=True, is_superuser=True,
        )
        self._address(admin, "Santiago", "Pagadian City")

        instructors = {}
        for spec in INSTRUCTORS:
            u = self._user(
                username=spec["username"], email=spec["email"], password=password,
                role=User.Role.INSTRUCTOR, status=spec["status"],
                first_name=spec["first_name"], last_name=spec["last_name"],
            )
            self._address(u, spec["barangay"], spec["city"])
            instructors[spec["username"]] = u

        students = {}
        for spec in STUDENTS:
            u = self._user(
                username=spec["username"], email=spec["email"], password=password,
                role=User.Role.STUDENT, status=spec["status"],
                first_name=spec["first_name"], last_name=spec["last_name"],
                device_id=device_for(spec["username"]) if spec["bound"] else None,
            )
            self._address(u, spec["barangay"], spec["city"])
            StudentProfile.objects.get_or_create(
                user=u,
                defaults=dict(
                    student_id_number=spec["sid"], course_program=spec["course"], year_level=spec["year"],
                    emergency_contact_name=f"Guardian of {spec['first_name']}",
                    emergency_contact_number="0917-000-0000",
                ),
            )
            students[spec["key"]] = u

        self.stdout.write(self.style.MIGRATE_HEADING("Departments & subjects"))
        dept, _ = Department.objects.get_or_create(code="CCS", defaults={"name": "College of Computer Studies"})
        teacher = instructors["jdelacruz"]
        subjects = {}
        for spec in SUBJECTS:
            subj, created = Subject.objects.get_or_create(
                subject_code=spec["code"],
                defaults=dict(title=spec["title"], teacher=teacher, department=dept),
            )
            subjects[spec["code"]] = subj
            for key in spec["students"]:
                # Signal-based auto-promotion only affects *pending* students.
                SubjectEnrollment.objects.get_or_create(subject=subj, student=students[key])
            self.stdout.write(f"  {'created' if created else 'exists '} {subj}")

        self.stdout.write(self.style.MIGRATE_HEADING("Class sessions & attendance history"))
        today = timezone.localdate()
        late_after = CONF["LATE_THRESHOLD_MINUTES"]
        last_session = {}
        n_logs = 0
        for code, subj in subjects.items():
            pattern_by_student = HISTORY[code]
            # CS101 meets in the afternoon so the two subjects don't share timestamps.
            hour = 8 if code == "IS201" else 13
            for week in range(weeks):
                day = today - timedelta(days=7 * (weeks - week))
                title = f"Week {week + 1} – {subj.title}"
                created_at = self._aware(day, hour)
                session, created = ClassSession.objects.get_or_create(
                    subject=subj, session_title=title,
                    defaults=dict(
                        date=day, expires_at=created_at + timedelta(hours=2),
                        instructor_latitude=DEMO_LAT, instructor_longitude=DEMO_LNG,
                        allowed_radius_meters=CONF["DEFAULT_RADIUS_METERS"], is_active=False,
                    ),
                )
                if created:  # created_at is auto_now_add – backdate it
                    ClassSession.objects.filter(pk=session.pk).update(created_at=created_at)
                    session.created_at = created_at
                last_session[code] = session

                for key, letters in pattern_by_student.items():
                    status = CODE[letters[week % len(letters)]]
                    student = students[key]
                    if AttendanceLog.objects.filter(session=session, student=student).exists():
                        continue
                    if status == ABSENT:
                        log = AttendanceLog.objects.create(
                            session=session, student=student, status=ABSENT, scanned_device_id="",
                            notes="Auto-marked absent when session closed.",
                        )
                        scanned = created_at + timedelta(hours=2)
                    else:
                        minutes = (3 + week * 2) % 11 if status == PRESENT else late_after + 3 + (week * 3) % 20
                        scanned = created_at + timedelta(minutes=minutes, seconds=7 * week)
                        log = AttendanceLog.objects.create(
                            session=session, student=student, status=status,
                            scanned_device_id=student.device_id or device_for(student.username),
                            notes="Late – arrived after the 15-minute grace period." if status == LATE else "",
                        )
                    AttendanceLog.objects.filter(pk=log.pk).update(scanned_at=scanned)
                    n_logs += 1
        self.stdout.write(f"  {n_logs} new attendance log(s)")

        self.stdout.write(self.style.MIGRATE_HEADING("Audit / anomaly feed"))
        if AuditScanLog.objects.filter(raw_payload__contains=SEED_MARKER).exists():
            self.stdout.write("  demo audit rows already present")
        else:
            self._seed_audit(last_session, students)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Seed complete."))
        self.stdout.write(f"  Password for newly created accounts: {password}")
        self.stdout.write("  Sign in with e-mail or username:")
        self.stdout.write("    Superadmin        admin@school.edu.ph")
        self.stdout.write("    Instructor        jdelacruz@school.edu.ph   (active; teaches IS201 & CS101)")
        self.stdout.write("    Instructor        mreyes@school.edu.ph      (pending – approve from the admin dashboard)")
        self.stdout.write("    Students          asantos / bramirez / cmendoza / dvillanueva (device unbound) / egarcia (pending)")

    # ------------------------------------------------------------------ audit rows
    def _seed_audit(self, last_session, students):
        is201, cs101 = last_session["IS201"], last_session["CS101"]
        now = timezone.now()

        def row(reason, session, student, minutes_ago, lat=None, lng=None, device=None):
            payload = json.dumps({"source": SEED_MARKER, "reason": reason})
            a = AuditScanLog.objects.create(
                session=session, student=student, reason=reason,
                student_latitude=lat, student_longitude=lng,
                submitted_device_id=device, raw_payload=payload,
            )
            AuditScanLog.objects.filter(pk=a.pk).update(created_at=now - timedelta(minutes=minutes_ago))

        R = AuditScanLog.Reason
        near = (DEMO_LAT + Decimal("0.000100"), DEMO_LNG + Decimal("0.000100"))
        far = (DEMO_LAT + Decimal("0.004500"), DEMO_LNG)  # ≈ 500 m north of the classroom
        # Elena scanned a class she isn't enrolled in → shows in the instructor's rogue-scan feed.
        row(R.UNENROLLED_STUDENT, is201, students["elena"], 25, *near, device_for("egarcia"))
        # Ben tried from a different phone than the one bound to his account (proxy attempt).
        row(R.DEVICE_MISMATCH, is201, students["ben"], 90, *near, str(uuid.uuid4()))
        # Carla scanned from outside the classroom radius.
        row(R.OUT_OF_BOUNDS, cs101, students["carla"], 240, *far, device_for("cmendoza"))
        # Someone replayed a stale (screenshotted) QR code.
        row(R.EXPIRED_TOKEN, is201, students["diego"], 600, *near, str(uuid.uuid4()))
        # Scan attempted after the instructor ended the session.
        row(R.SESSION_CLOSED, cs101, students["ana"], 1500, *near, device_for("asantos"))
        self.stdout.write("  5 demo audit rows created")
