"""JSON API consumed by the instructor projection screen and the student scanner."""
from datetime import timedelta

import segno
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import AttendanceLog, AuditScanLog, ClassSession, Subject, SubjectEnrollment, User
from .permissions import IsInstructorOrSuperadmin, IsStudent
from .serializers import AttendanceUpdateSerializer, EnrollSerializer, ScanSerializer, StartSessionSerializer
from .directory import StudentDirectory, full_name
from .dsa import merge_sort
from .services import (
    approve_rogue_scan,
    auto_close_if_expired,
    enroll_students,
    finalize_session,
    punctuality_summary,
    rogue_scans_for_session,
    set_attendance,
    to_coord,
    verify_and_record_scan,
)
from .tokens import issue_qr_token

CONF = settings.SMARTATTENDANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _owned_subject_q(user):
    return Q() if user.is_superadmin else Q(teacher=user)


def get_owned_subject(user, pk):
    return get_object_or_404(Subject.objects.filter(_owned_subject_q(user)), pk=pk)


def get_owned_session(user, pk):
    qs = ClassSession.objects.select_related("subject")
    if not user.is_superadmin:
        qs = qs.filter(subject__teacher=user)
    return get_object_or_404(qs, pk=pk)


def iso(dt):
    return timezone.localtime(dt).isoformat() if dt else None


def session_payload(session):
    now = timezone.now()
    return {
        "id": session.pk,
        "title": session.session_title,
        "subject_id": session.subject_id,
        "subject_code": session.subject.subject_code,
        "subject_title": session.subject.title,
        "date": session.date.isoformat(),
        "created_at": iso(session.created_at),
        "expires_at": iso(session.expires_at),
        "is_active": session.is_active,
        "is_open": session.is_open,
        "seconds_remaining": max(0, int((session.expires_at - now).total_seconds())) if session.is_open else 0,
        "has_geofence": session.has_geofence,
        "allowed_radius_meters": session.allowed_radius_meters,
        "late_after_minutes": session.late_after_minutes,
    }


class ScanThrottle(UserRateThrottle):
    rate = "30/min"


# ---------------------------------------------------------------------------
# Instructor – sessions
# ---------------------------------------------------------------------------
class StartSessionView(APIView):
    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request):
        ser = StartSessionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        subject = get_owned_subject(request.user, d["subject_id"])

        # Geofencing is a per-session choice. OFF => no coordinates are stored, so the scan pipeline
        # skips the Haversine check (ClassSession.has_geofence is False) and ignores student GPS.
        geofence_on = d["geofence_enabled"]
        lat = to_coord(d.get("latitude"), 90) if geofence_on else None
        lng = to_coord(d.get("longitude"), 180) if geofence_on else None
        if geofence_on and (lat is None or lng is None):
            return Response({"ok": False, "detail": "A valid location is required when geofencing is on."},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # Only one live session per subject – close any previous one first.
            for old in ClassSession.objects.filter(subject=subject, is_active=True):
                finalize_session(old)
            now = timezone.now()
            session = ClassSession.objects.create(
                subject=subject,
                session_title=d.get("session_title") or f"{subject.subject_code} – {timezone.localtime(now):%b %d, %Y %I:%M %p}",
                date=timezone.localdate(),
                expires_at=now + timedelta(minutes=d["duration_minutes"]),
                instructor_latitude=lat,
                instructor_longitude=lng,
                allowed_radius_meters=d["allowed_radius_meters"],
                late_after_minutes=d["late_after_minutes"],
                is_active=True,
            )
        return Response(
            {"ok": True, "session": session_payload(session), "redirect": f"/instructor/sessions/{session.pk}/"},
            status=status.HTTP_201_CREATED,
        )


class QRTokenView(APIView):
    """Polled every ~8 s by the projection screen; returns a fresh 10 s JWT + SVG QR."""

    permission_classes = [IsInstructorOrSuperadmin]

    def get(self, request, pk):
        session = auto_close_if_expired(get_owned_session(request.user, pk))
        if not session.is_open:
            return Response({"ok": False, "session": session_payload(session),
                             "detail": "Session is closed."}, status=status.HTTP_410_GONE)
        token, exp = issue_qr_token(session)
        svg = segno.make(token, error="m").svg_inline(scale=6, border=2, dark="#0f172a", omitsize=True)
        return Response({
            "ok": True,
            "token": token,
            "svg": svg,
            "token_expires_at": iso(exp),
            "ttl_seconds": CONF["QR_TOKEN_TTL_SECONDS"],
            "refresh_seconds": CONF["QR_REFRESH_SECONDS"],
            "session": session_payload(session),
        })


class SessionLiveView(APIView):
    """Live roster, rogue-scan feed and security alerts for one session."""

    permission_classes = [IsInstructorOrSuperadmin]

    def get(self, request, pk):
        session = auto_close_if_expired(get_owned_session(request.user, pk))
        logs = {
            log.student_id: log
            for log in AttendanceLog.objects.filter(session=session)
        }
        # Alphabetical roster, sorted in Python with merge sort.
        enrollments = merge_sort(
            SubjectEnrollment.objects.filter(subject_id=session.subject_id)
            .select_related("student", "student__student_profile"),
            key=lambda e: (e.student.last_name.casefold(), e.student.first_name.casefold(), e.student.username),
        )
        roster, counts = [], {"present": 0, "late": 0, "absent": 0, "not_scanned": 0}
        for e in enrollments:
            st = e.student
            log = logs.get(st.pk)
            counts[log.status if log else "not_scanned"] += 1
            roster.append({
                "student_id": st.pk,
                "name": st.get_full_name() or st.username,
                "id_number": st.id_number,
                "status": log.status if log else None,
                "notes": log.notes if log else "",
                "scanned_at": iso(log.scanned_at) if log and log.scanned_device_id else None,
                "device_bound": bool(st.device_id),
                "account_status": st.status,
            })
        counts["enrolled"] = len(roster)

        rogue = [{  # FIFO queue: position 1 arrived first and is next in line
            "position": position,
            "audit_id": a.pk,
            "student_id": a.student_id,
            "name": a.student.get_full_name() or a.student.username,
            "id_number": a.student.id_number,
            "course": getattr(getattr(a.student, "student_profile", None), "course_program", ""),
            "account_status": a.student.status,
            "scanned_at": iso(a.created_at),
        } for position, a in enumerate(rogue_scans_for_session(session), start=1)]

        alerts = [{
            "id": a.pk,
            "reason": a.reason,
            "reason_display": a.get_reason_display(),
            "name": (a.student.get_full_name() or a.student.username) if a.student else "Unknown",
            "created_at": iso(a.created_at),
        } for a in AuditScanLog.objects.filter(session=session)
            .exclude(reason=AuditScanLog.Reason.UNENROLLED_STUDENT)
            .select_related("student")[:15]]

        return Response({"ok": True, "session": session_payload(session), "counts": counts,
                         "roster": roster, "rogue": rogue, "alerts": alerts})


class CloseSessionView(APIView):
    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request, pk):
        session = finalize_session(get_owned_session(request.user, pk))
        return Response({"ok": True, "session": session_payload(session)})


class AttendanceUpdateView(APIView):
    """Inline status toggle / notes edit (creates a manual log if the student never scanned)."""

    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request, pk):
        session = get_owned_session(request.user, pk)
        ser = AttendanceUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            log, _ = set_attendance(session, d["student_id"], status=d.get("status"), notes=d.get("notes"))
        except ValueError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "status": log.status, "notes": log.notes})


class ApproveRogueScanView(APIView):
    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request, pk, audit_id):
        session = get_owned_session(request.user, pk)
        audit = get_object_or_404(
            AuditScanLog.objects.select_related("student"),
            pk=audit_id, session=session, reason=AuditScanLog.Reason.UNENROLLED_STUDENT,
        )
        try:
            log, _ = approve_rogue_scan(session, audit)
        except ValueError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"ok": True, "status": log.status,
                         "detail": f"{audit.student} added to {session.subject.subject_code} and marked {log.get_status_display()}."})


# ---------------------------------------------------------------------------
# Instructor – roster management
# ---------------------------------------------------------------------------
class EnrollStudentView(APIView):
    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request, pk):
        subject = get_owned_subject(request.user, pk)
        ser = EnrollSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        directory = StudentDirectory.load()   # merge-sorted ID list + hash index, built once
        if d["identifier_list"]:
            return self._bulk(subject, d["identifier_list"], directory)
        student = directory.find(d["identifier"])
        if student is None:
            return Response({"ok": False, "detail": "No active student matches that ID, username or e-mail."},
                            status=status.HTTP_404_NOT_FOUND)
        if student.is_suspended:
            return Response({"ok": False, "detail": "This student account is suspended."},
                            status=status.HTTP_400_BAD_REQUEST)
        _, created = SubjectEnrollment.objects.get_or_create(subject=subject, student=student)
        msg = f"{student} enrolled in {subject.subject_code}." if created else f"{student} is already enrolled."
        return Response({"ok": True, "created": created, "detail": msg})

    @staticmethod
    def _bulk(subject, identifiers, directory):
        r = enroll_students(subject, identifiers, directory)
        parts = [f"{len(r['enrolled'])} enrolled"]
        if r["already"]:
            parts.append(f"{len(r['already'])} already enrolled")
        if r["suspended"]:
            parts.append(f"{len(r['suspended'])} suspended")
        if r["not_found"]:
            parts.append(f"{len(r['not_found'])} not found ({', '.join(r['not_found'][:10])}"
                         f"{'…' if len(r['not_found']) > 10 else ''})")
        return Response({
            "ok": True, "created": bool(r["enrolled"]), "detail": "; ".join(parts) + ".",
            "enrolled": [full_name(s) for s in r["enrolled"]],
            "already": [full_name(s) for s in r["already"]],
            "suspended": [full_name(s) for s in r["suspended"]],
            "not_found": r["not_found"],
        })


class DropStudentView(APIView):
    """Drop / Unenroll – removes only the SubjectEnrollment row. The User account is untouched."""

    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request, pk, student_id):
        subject = get_owned_subject(request.user, pk)
        deleted, _ = SubjectEnrollment.objects.filter(subject=subject, student_id=student_id).delete()
        if not deleted:
            return Response({"ok": False, "detail": "Student is not enrolled in this subject."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"ok": True, "detail": f"Student dropped from {subject.subject_code}. "
                                               "Their account and past attendance records are preserved."})


class UnbindDeviceView(APIView):
    permission_classes = [IsInstructorOrSuperadmin]

    def post(self, request, student_id):
        qs = User.objects.filter(role=User.Role.STUDENT)
        if not request.user.is_superadmin:
            qs = qs.filter(enrolled_subjects__subject__teacher=request.user).distinct()
        student = get_object_or_404(qs, pk=student_id)
        student.device_id = None
        student.save(update_fields=["device_id"])
        return Response({"ok": True, "detail": f"Device unbound for {student}. The next scan will bind a new phone."})


class StudentSubjectProfileView(APIView):
    permission_classes = [IsInstructorOrSuperadmin]

    def get(self, request, pk, student_id):
        subject = get_owned_subject(request.user, pk)
        student = get_object_or_404(User.objects.select_related("student_profile"),
                                    pk=student_id, role=User.Role.STUDENT)
        logs = AttendanceLog.objects.filter(session__subject=subject, student=student)
        summary = punctuality_summary(logs)
        recent = [{
            "date": l.session.date.isoformat(),
            "title": l.session.session_title,
            "status": l.status,
            "notes": l.notes,
        } for l in logs.select_related("session").order_by("-session__date", "-session__created_at")[:10]]
        enrolled = SubjectEnrollment.objects.filter(subject=subject, student=student).first()
        return Response({
            "ok": True,
            "student": {
                "id": student.pk,
                "name": student.get_full_name() or student.username,
                "id_number": student.id_number,
                "course": getattr(getattr(student, "student_profile", None), "course_program", ""),
                "year_level": getattr(getattr(student, "student_profile", None), "year_level", None),
                "email": student.email,
                "account_status": student.status,
                "device_bound": bool(student.device_id),
                "enrolled_at": iso(enrolled.enrolled_at) if enrolled else None,
            },
            "subject": {"id": subject.pk, "code": subject.subject_code, "title": subject.title},
            "summary": summary,
            "recent": recent,
        })


# ---------------------------------------------------------------------------
# Student – scan
# ---------------------------------------------------------------------------
class ScanAttendanceView(APIView):
    permission_classes = [IsAuthenticated, IsStudent]
    throttle_classes = [ScanThrottle]

    def post(self, request):
        ser = ScanSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"ok": False, "code": "bad_request", "message": "Malformed scan payload.",
                             "errors": ser.errors}, status=status.HTTP_400_BAD_REQUEST)
        result = verify_and_record_scan(request.user, ser.validated_data)
        body = {"ok": result.ok, "code": result.code, "message": result.message,
                "device_bound_now": result.device_bound_now}
        if result.distance_m is not None:
            body["distance_m"] = round(result.distance_m, 1)
        if result.session:
            body["subject"] = f"{result.session.subject.subject_code} – {result.session.subject.title}"
        if result.attendance:
            body["status"] = result.attendance.status
            body["scanned_at"] = iso(result.attendance.scanned_at)
        return Response(body, status=result.http_status)
