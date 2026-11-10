import csv

from django.conf import settings
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from ..decorators import instructor_required
from ..models import AttendanceLog, ClassSession, Subject, SubjectEnrollment, User
from ..directory import full_name
from ..exports import XLSX_CONTENT_TYPE, build_subject_workbook
from ..dsa import count_by_group, linear_search
from ..services import (
    ROSTER_SORTS, STATUSES, auto_close_if_expired, close_stale_sessions, punctuality_summary, rank_by_attendance, sort_roster,
    student_rows_with_standing, summary_from_counts,
)


@instructor_required
def dashboard(request):
    user = request.user
    subjects = list(
        Subject.objects.filter(teacher=user).select_related("department").annotate(
            n_students=Count("enrollments", distinct=True),
            n_sessions=Count("sessions", distinct=True),
        )
    )
    close_stale_sessions([s.pk for s in subjects])
    now = timezone.now()
    # One pass over this instructor's attendance records, tallied per subject (dictionary of dictionaries),
    # instead of one count per subject.
    tallies = count_by_group(
        AttendanceLog.objects.filter(session__subject__teacher=user).values_list("session__subject_id", "status"),
        initial=STATUSES,
    )
    totals = {k: 0 for k in STATUSES}
    for s in subjects:
        tally = tallies.get(s.pk, {})
        s.stats = summary_from_counts(tally)
        for k in STATUSES:
            totals[k] += tally.get(k, 0)
        s.live_session = s.sessions.filter(is_active=True, expires_at__gte=now).first()

    overall = summary_from_counts(totals)
    recent_sessions = (
        ClassSession.objects.filter(subject__teacher=user).select_related("subject")
        .annotate(
            n_present=Count("attendance_logs", filter=Q(attendance_logs__status="present")),
            n_late=Count("attendance_logs", filter=Q(attendance_logs__status="late")),
            n_absent=Count("attendance_logs", filter=Q(attendance_logs__status="absent")),
        )[:8]
    )
    return render(request, "instructor/dashboard.html", {
        "subjects": subjects,
        "overall": overall,
        "recent_sessions": recent_sessions,
        "total_students": SubjectEnrollment.objects.filter(subject__teacher=user)
        .values("student").distinct().count(),
    })


@instructor_required
def subject_detail(request, pk):
    subject = get_object_or_404(Subject.objects.select_related("department"), pk=pk, teacher=request.user)
    close_stale_sessions([subject.pk])
    students = User.objects.filter(enrolled_subjects__subject=subject).select_related("student_profile")
    subject_logs = AttendanceLog.objects.filter(session__subject=subject)
    roster = student_rows_with_standing(students, subject_logs)   # one-pass counting per student
    rank_by_attendance(roster)                                     # merge sort + competition ranking
    q = request.GET.get("q", "").strip()[:100]
    sort = request.GET.get("sort", "name")
    shown = linear_search(roster, q, fields=(full_name, lambda s: s.id_number, lambda s: s.username)) if q else roster
    shown = sort_roster(shown, sort)                               # merge sort by the chosen column
    sessions = subject.sessions.annotate(
        n_present=Count("attendance_logs", filter=Q(attendance_logs__status="present")),
        n_late=Count("attendance_logs", filter=Q(attendance_logs__status="late")),
        n_absent=Count("attendance_logs", filter=Q(attendance_logs__status="absent")),
    )[:20]
    return render(request, "instructor/subject_detail.html", {
        "subject": subject,
        "subjects": [subject],
        "roster": shown,
        "enrolled_count": len(roster),
        "q": q,
        "sort": sort if sort in ROSTER_SORTS else "name",
        "sort_options": [(k, label) for k, (label, _) in ROSTER_SORTS.items()],
        "sessions": sessions,
        "stats": punctuality_summary(subject_logs),
        "live_session": subject.sessions.filter(is_active=True, expires_at__gte=timezone.now()).first(),
    })


@instructor_required
def session_live(request, pk):
    session = get_object_or_404(ClassSession.objects.select_related("subject"), pk=pk,
                                subject__teacher=request.user)
    session = auto_close_if_expired(session)
    conf = settings.SMARTATTENDANCE
    return render(request, "instructor/session_live.html", {
        "session": session,
        "refresh_seconds": conf["QR_REFRESH_SECONDS"],
        "live_poll_seconds": conf["LIVE_POLL_SECONDS"],
    })


@instructor_required
def session_export(request, pk):
    session = get_object_or_404(ClassSession.objects.select_related("subject"), pk=pk,
                                subject__teacher=request.user)
    resp = HttpResponse(content_type="text/csv")
    fname = f"{session.subject.subject_code}_{session.date:%Y%m%d}_session{session.pk}.csv"
    resp["Content-Disposition"] = f'attachment; filename="{fname}"'
    w = csv.writer(resp)
    w.writerow(["Student ID", "Last name", "First name", "Status", "Scanned at", "Notes"])
    logs = {l.student_id: l for l in session.attendance_logs.all()}
    for e in SubjectEnrollment.objects.filter(subject=session.subject).select_related(
        "student", "student__student_profile"
    ).order_by("student__last_name"):
        st, log = e.student, logs.get(e.student_id)
        w.writerow([
            st.id_number, st.last_name, st.first_name,
            log.get_status_display() if log else "Not scanned",
            timezone.localtime(log.scanned_at).strftime("%Y-%m-%d %H:%M:%S") if log and log.scanned_device_id else "",
            log.notes if log else "",
        ])
    return resp


@instructor_required
def subject_export_xlsx(request, pk):
    """Excel class record (summary, attendance grid, sessions) of one of the instructor's subjects."""
    subject = get_object_or_404(Subject.objects.select_related("teacher"), pk=pk, teacher=request.user)
    close_stale_sessions([subject.pk])            # finalize expired sessions so absences are included
    stamp = timezone.localtime().strftime("%Y%m%d")
    return xlsx_response(build_subject_workbook(subject), f"{subject.subject_code}_class_record_{stamp}.xlsx")


def xlsx_response(data, filename):
    resp = HttpResponse(data, content_type=XLSX_CONTENT_TYPE)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
