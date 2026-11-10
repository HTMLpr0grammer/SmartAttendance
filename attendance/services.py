"""
Business logic for SmartAttendance.

Keeping this outside the views means the API, the server-rendered pages,
the Django admin and the seed script all share the exact same rules.
"""
import json
import math
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from .dsa import Queue, competition_rank, count_by, count_by_group, merge_sort
from .models import AttendanceLog, AuditScanLog, ClassSession, SubjectEnrollment, User
from .tokens import TokenError, decode_qr_token

CONF = settings.SMARTATTENDANCE
EARTH_RADIUS_M = 6_371_000
DEVICE_IN_USE_MESSAGE = (
    "This phone is already linked to another student's account. Each phone can be used by only one student. "
    "If this is now your phone, ask your instructor or the administrator to unlink it."
)


# ---------------------------------------------------------------------------
# Geo
# ---------------------------------------------------------------------------
def haversine_meters(lat1, lon1, lat2, lon2):
    """Great-circle distance between two WGS84 points, in metres."""
    lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def to_coord(value, limit):
    """Parse a latitude/longitude into a 6-dp Decimal, or None if invalid."""
    if value in (None, ""):
        return None
    try:
        dec = Decimal(str(value)).quantize(Decimal("0.000001"))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if abs(dec) > limit:
        return None
    return dec


# ---------------------------------------------------------------------------
# Attendance helpers
# ---------------------------------------------------------------------------
def status_for_time(session, when):
    minutes = session.late_after_minutes if session.late_after_minutes is not None else CONF["LATE_THRESHOLD_MINUTES"]
    threshold = session.created_at + timedelta(minutes=minutes)
    return AttendanceLog.Status.LATE if when > threshold else AttendanceLog.Status.PRESENT


def is_enrolled(subject_id, student_id):
    return SubjectEnrollment.objects.filter(subject_id=subject_id, student_id=student_id).exists()


@transaction.atomic
def finalize_session(session):
    """Close a session and mark every enrolled student without a log as absent."""
    session = ClassSession.objects.select_for_update().get(pk=session.pk)
    if session.is_active:
        session.is_active = False
        now = timezone.now()
        if session.expires_at > now:
            session.expires_at = now
        session.save(update_fields=["is_active", "expires_at"])

    scanned = AttendanceLog.objects.filter(session=session).values_list("student_id", flat=True)
    missing = (
        SubjectEnrollment.objects.filter(subject_id=session.subject_id)
        .exclude(student_id__in=scanned)
        .values_list("student_id", flat=True)
    )
    AttendanceLog.objects.bulk_create(
        [
            AttendanceLog(
                session=session,
                student_id=sid,
                status=AttendanceLog.Status.ABSENT,
                scanned_device_id="",
                notes="Auto-marked absent when session closed.",
            )
            for sid in missing
        ],
        ignore_conflicts=True,
    )
    return session


def auto_close_if_expired(session):
    if session.is_active and timezone.now() > session.expires_at:
        return finalize_session(session)
    return session


def close_stale_sessions(subject_ids=None):
    qs = ClassSession.objects.filter(is_active=True, expires_at__lt=timezone.now())
    if subject_ids is not None:
        qs = qs.filter(subject_id__in=subject_ids)
    for session in qs:
        finalize_session(session)


# ---------------------------------------------------------------------------
# Scan verification pipeline
# ---------------------------------------------------------------------------
@dataclass
class ScanResult:
    ok: bool
    message: str
    http_status: int = 200
    code: str = "ok"
    attendance: AttendanceLog | None = None
    session: ClassSession | None = None
    device_bound_now: bool = False
    distance_m: float | None = None


def _audit(reason, *, session=None, student=None, lat=None, lng=None, device_id=None, payload=None):
    return AuditScanLog.objects.create(
        session=session,
        student=student,
        reason=reason,
        student_latitude=lat,
        student_longitude=lng,
        submitted_device_id=(device_id or None),
        raw_payload=payload or "",
    )


def verify_and_record_scan(student, data):
    """
    Run the anti-proxy pipeline in strict order:
      1. JWT signature & expiry       → expired_token
      2. Session active / not expired → session_closed
      3. Hardware (device) binding    → device_mismatch
      4. Haversine geofence           → out_of_bounds
      5. Enrollment                   → unenrolled_student
      6. Record present / late
    """
    token = data.get("token")
    device_id = str(data.get("device_id") or "").strip()[:255]
    lat = to_coord(data.get("latitude"), 90)
    lng = to_coord(data.get("longitude"), 180)
    raw = json.dumps(
        {"token": token, "latitude": data.get("latitude"), "longitude": data.get("longitude"),
         "device_id": device_id},
        default=str,
    )[:4000]
    audit_kw = dict(student=student, lat=lat, lng=lng, device_id=device_id, payload=raw)

    # Pre-flight: only students in good standing may scan.
    if not student.is_student:
        return ScanResult(False, "Only student accounts can scan attendance.", 403, "not_student")
    if student.is_suspended or not student.is_active:
        return ScanResult(False, "Your account is suspended. Please contact the registrar.", 403, "suspended")
    if not device_id:
        return ScanResult(False, "Missing device identifier. Please reload the scanner page.", 400, "no_device")

    # 1. Decode & verify JWT -------------------------------------------------
    try:
        claims = decode_qr_token(token)
    except TokenError as exc:
        session = ClassSession.objects.filter(pk=exc.session_id).first() if exc.session_id else None
        _audit(AuditScanLog.Reason.EXPIRED_TOKEN, session=session, **audit_kw)
        return ScanResult(False, str(exc), 400, "expired_token", session=session)

    session = ClassSession.objects.select_related("subject").filter(pk=claims["session_id"]).first()
    if session is None:
        _audit(AuditScanLog.Reason.EXPIRED_TOKEN, **audit_kw)
        return ScanResult(False, "This QR code refers to an unknown session.", 400, "expired_token")

    # 2. Session status --------------------------------------------------------
    if not session.is_open:
        _audit(AuditScanLog.Reason.SESSION_CLOSED, session=session, **audit_kw)
        return ScanResult(False, "This class session is already closed.", 403, "session_closed", session=session)

    # 3. Anti-proxy hardware lock ---------------------------------------------
    #    One student per phone and one phone per student.
    bound_now = False
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=student.pk)
        if not locked.device_id:
            taken = User.objects.filter(device_id=device_id).exclude(pk=student.pk).exists()
            if not taken:
                locked.device_id = device_id
                try:
                    with transaction.atomic():
                        locked.save(update_fields=["device_id"])
                except IntegrityError:  # another account bound this phone at the same moment
                    taken = True
            if taken:
                _audit(AuditScanLog.Reason.DEVICE_MISMATCH, session=session, **audit_kw)
                return ScanResult(False, DEVICE_IN_USE_MESSAGE, 403, "device_in_use", session=session)
            bound_now = True
        elif locked.device_id != device_id:
            _audit(AuditScanLog.Reason.DEVICE_MISMATCH, session=session, **audit_kw)
            return ScanResult(
                False, "Device mismatch. Bound to another phone.", 403, "device_mismatch", session=session
            )
    student.device_id = device_id

    # 4. Geofence (Haversine) -------------------------------------------------
    distance = None
    if session.has_geofence:
        if lat is None or lng is None:
            _audit(AuditScanLog.Reason.OUT_OF_BOUNDS, session=session, **audit_kw)
            return ScanResult(
                False, "Location is required. Please allow GPS access and try again.", 403,
                "out_of_bounds", session=session, device_bound_now=bound_now,
            )
        distance = haversine_meters(session.instructor_latitude, session.instructor_longitude, lat, lng)
        if distance > session.allowed_radius_meters:
            _audit(AuditScanLog.Reason.OUT_OF_BOUNDS, session=session, **audit_kw)
            return ScanResult(
                False, "Out of classroom boundaries.", 403, "out_of_bounds",
                session=session, device_bound_now=bound_now, distance_m=distance,
            )

    # 5. Enrollment -----------------------------------------------------------
    if not is_enrolled(session.subject_id, student.pk):
        _audit(AuditScanLog.Reason.UNENROLLED_STUDENT, session=session, **audit_kw)
        return ScanResult(
            False, "Not enrolled in this subject.", 403, "unenrolled_student",
            session=session, device_bound_now=bound_now, distance_m=distance,
        )

    # 6. Record attendance ----------------------------------------------------
    existing = AttendanceLog.objects.filter(session=session, student=student).first()
    if existing:
        return ScanResult(
            True, f"Already checked in as {existing.get_status_display()}.", 200, "duplicate",
            attendance=existing, session=session, distance_m=distance,
        )
    try:
        log = AttendanceLog.objects.create(
            session=session,
            student=student,
            status=status_for_time(session, timezone.now()),
            scanned_device_id=device_id,
        )
    except IntegrityError:  # concurrent double-submit
        log = AttendanceLog.objects.get(session=session, student=student)
    label = "Present" if log.status == AttendanceLog.Status.PRESENT else "Late"
    return ScanResult(
        True, f"Attendance recorded: {label} – {session.subject.subject_code}.", 201, "recorded",
        attendance=log, session=session, device_bound_now=bound_now, distance_m=distance,
    )


# ---------------------------------------------------------------------------
# Instructor quick actions
# ---------------------------------------------------------------------------
@transaction.atomic
def approve_rogue_scan(session, audit):
    """[Add to Class & Approve]: enroll, promote (via signal) and log attendance."""
    student = audit.student
    if student is None or not student.is_student:
        raise ValueError("This scan is not linked to a student account.")
    if student.is_suspended or not student.is_active:
        raise ValueError("Suspended or archived students cannot be enrolled.")
    SubjectEnrollment.objects.get_or_create(subject_id=session.subject_id, student=student)
    log, created = AttendanceLog.objects.get_or_create(
        session=session,
        student=student,
        defaults={
            "status": status_for_time(session, audit.created_at),
            "scanned_device_id": audit.submitted_device_id or "",
            "notes": "Added to class from rogue-scan feed.",
        },
    )
    return log, created


def rogue_scans_for_session(session):
    """FIFO queue of students who scanned but are not enrolled, in the order they first arrived.

    Each student appears once (a set remembers who is already queued), using their *first* attempt,
    so the student who arrived first is first in line for "Add to Class & Approve".
    """
    enrolled_ids = set(
        SubjectEnrollment.objects.filter(subject_id=session.subject_id).values_list("student_id", flat=True)
    )
    audits = (
        AuditScanLog.objects.filter(session=session, reason=AuditScanLog.Reason.UNENROLLED_STUDENT)
        .exclude(student__isnull=True)
        .select_related("student", "student__student_profile")
        .order_by("created_at", "pk")
    )
    queue, queued = Queue(), set()
    for audit in audits:
        if audit.student_id in enrolled_ids or audit.student_id in queued:
            continue
        queued.add(audit.student_id)
        queue.enqueue(audit)
    return queue


def set_attendance(session, student_id, status=None, notes=None):
    """Add or update one attendance record (manual entry by an instructor or the command-line menu)."""
    if not SubjectEnrollment.objects.filter(subject_id=session.subject_id, student_id=student_id).exists():
        raise ValueError("Student is not enrolled in this subject.")
    if status is not None and status not in AttendanceLog.Status.values:
        raise ValueError("Status must be present, late or absent.")
    log, created = AttendanceLog.objects.get_or_create(
        session=session, student_id=student_id,
        defaults={"status": status or AttendanceLog.Status.PRESENT, "scanned_device_id": "", "notes": notes or ""},
    )
    if not created:
        fields = []
        if status is not None:
            log.status = status
            fields.append("status")
        if notes is not None:
            log.notes = notes
            fields.append("notes")
        if fields:
            log.save(update_fields=fields)
    return log, created


def enroll_students(subject, identifiers, directory=None):
    """Enroll many students at once by ID number, username or e-mail.

    The directory is built once (merge sort + hash index); each identifier is then found with binary
    search or a dictionary look-up, so n identifiers among m students cost O(m log m + n log m) instead
    of n separate database queries.
    """
    from .directory import StudentDirectory  # local import: directory imports models only

    directory = directory or StudentDirectory.load()
    already = set(SubjectEnrollment.objects.filter(subject=subject).values_list("student_id", flat=True))
    result = {"enrolled": [], "already": [], "not_found": [], "suspended": []}
    with transaction.atomic():
        for ident in identifiers:
            student = directory.find(ident)
            if student is None:
                result["not_found"].append(ident)
            elif student.is_suspended:
                result["suspended"].append(student)
            elif student.pk in already:
                result["already"].append(student)
            else:
                SubjectEnrollment.objects.create(subject=subject, student=student)
                already.add(student.pk)
                result["enrolled"].append(student)
    return result


# ---------------------------------------------------------------------------
# Punctuality analytics
# ---------------------------------------------------------------------------
def standing_for(present, late, absent):
    total = present + late + absent
    rate = round(((present + late) / total) * 100, 1) if total else 100.0
    if total and (absent >= CONF["CRITICAL_ABSENCES"] or rate < CONF["CRITICAL_ATTENDANCE_RATE"]):
        badge = "Critical Absences"
    elif late >= CONF["FREQUENT_TARDY_COUNT"] or (total and late / total >= 0.25 and late >= 2):
        badge = "Frequent Tardy"
    else:
        badge = "Good Standing"
    return rate, badge


STATUSES = ("present", "late", "absent")


def punctuality_summary(logs):
    """Present / late / absent totals and rates for a set of attendance records.

    The statuses are read into a list and counted in one pass with a dictionary (count_by).
    Accepts an AttendanceLog queryset or any iterable of logs.
    """
    statuses = logs.values_list("status", flat=True) if hasattr(logs, "values_list") else (l.status for l in logs)
    return summary_from_counts(count_by(statuses, initial=STATUSES))


def summary_from_counts(counts):
    present, late, absent = counts.get("present", 0), counts.get("late", 0), counts.get("absent", 0)
    total = present + late + absent
    rate, badge = standing_for(present, late, absent)
    return {
        "present": present,
        "late": late,
        "absent": absent,
        "total": total,
        "attended": present + late,
        "attendance_rate": rate,
        "on_time_rate": round(present / total * 100, 1) if total else 0.0,
        "late_rate": round(late / total * 100, 1) if total else 0.0,
        "absent_rate": round(absent / total * 100, 1) if total else 0.0,
        "standing": badge,
    }


def student_rows_with_standing(students, logs=None):
    """Attach present/late/absent counts, attendance rate and standing to each student.

    *logs* limits which records count (e.g. one subject); by default every record counts.
    All (student, status) pairs are tallied in one pass into a dictionary of dictionaries
    (count_by_group), then each student's tally is looked up in O(1).
    """
    logs = AttendanceLog.objects.all() if logs is None else logs
    tallies = count_by_group(logs.values_list("student_id", "status"), initial=STATUSES)
    empty = {k: 0 for k in STATUSES}
    rows = []
    for s in students:
        t = tallies.get(s.pk, empty)
        s.n_present, s.n_late, s.n_absent = t["present"], t["late"], t["absent"]
        s.n_total = s.n_present + s.n_late + s.n_absent
        s.attendance_rate, s.standing = standing_for(s.n_present, s.n_late, s.n_absent)
        rows.append(s)
    return rows


# ---------------------------------------------------------------------------
# Sorting and ranking rows (merge sort)
# ---------------------------------------------------------------------------
STANDING_ORDER = {"Critical Absences": 0, "Frequent Tardy": 1, "Good Standing": 2}


def _name_key(s):
    return ((s.last_name or s.username).casefold(), (s.first_name or "").casefold())


# Each option is a tuple key, so merge sort orders by the first field and breaks ties with the next.
ROSTER_SORTS = {
    "name": ("Name (A–Z)", lambda s: _name_key(s)),
    "id": ("Student ID", lambda s: ((s.id_number or "").casefold(), _name_key(s))),
    "rate": ("Attendance rate (highest first)", lambda s: (-s.attendance_rate, _name_key(s))),
    "late": ("Most late check-ins", lambda s: (-s.n_late, _name_key(s))),
    "absent": ("Most absences", lambda s: (-s.n_absent, _name_key(s))),
    "standing": ("Standing (needs attention first)",
                 lambda s: (STANDING_ORDER.get(s.standing, 3), s.attendance_rate, _name_key(s))),
}


def rank_by_attendance(rows):
    """Set row.rank: 1 for the highest attendance rate; equal rates share a rank."""
    ordered = merge_sort(rows, key=lambda s: (-s.attendance_rate, _name_key(s)))
    for rank, row in competition_rank(ordered, key=lambda s: s.attendance_rate):
        row.rank = rank
    return ordered


def sort_roster(rows, sort="name", counter=None):
    sort = sort if sort in ROSTER_SORTS else "name"
    return merge_sort(rows, key=ROSTER_SORTS[sort][1], counter=counter)


def watchlist(rows):
    """Students who are not in good standing: critical cases first, then the lowest attendance rate."""
    flagged = [r for r in rows if r.standing != "Good Standing"]
    return merge_sort(flagged, key=lambda r: (STANDING_ORDER.get(r.standing, 3), r.attendance_rate, _name_key(r)))
