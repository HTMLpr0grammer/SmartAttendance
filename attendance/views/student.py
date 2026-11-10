from django.shortcuts import render

from ..decorators import student_required
from ..models import AttendanceLog, Subject
from ..services import punctuality_summary


@student_required
def dashboard(request):
    user = request.user
    subjects = Subject.objects.filter(enrollments__student=user).select_related("teacher")
    logs = AttendanceLog.objects.filter(student=user).select_related("session__subject")

    subject_filter = request.GET.get("subject", "")
    filtered = logs.filter(session__subject_id=subject_filter) if subject_filter.isdigit() else logs

    per_subject = []
    for s in subjects:
        per_subject.append({"subject": s, "stats": punctuality_summary(logs.filter(session__subject=s))})

    return render(request, "student/dashboard.html", {
        "stats": punctuality_summary(filtered),
        "history": filtered.order_by("-session__date", "-scanned_at")[:100],
        "subjects": subjects,
        "per_subject": per_subject,
        "subject_filter": subject_filter,
    })


@student_required
def scan(request):
    return render(request, "student/scan.html")
