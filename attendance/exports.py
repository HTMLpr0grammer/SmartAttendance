"""
Excel (.xlsx) exports of attendance records, built with openpyxl.

    build_subject_workbook(subject)            class record of one subject (instructor and Superadmin)
    build_school_workbook(date_from, date_to, department)   institution-wide report (Superadmin)

The workbooks only *read* the database. Totals, rates, ranks and standings are computed with the same
counting, merge-sort and ranking code the dashboards use, so the files match what is shown on screen and
nothing derived is ever stored (the database stays in third normal form).
"""
from io import BytesIO

from django.conf import settings
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .directory import full_name
from .dsa import count_by_group, merge_sort
from .models import AttendanceLog, AuditScanLog, ClassSession, Subject, SubjectEnrollment, User
from .services import (
    STANDING_ORDER, STATUSES, rank_by_attendance, student_rows_with_standing, summary_from_counts,
)

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

BRAND = "16633F"
HEADER_FILL = PatternFill("solid", fgColor=BRAND)
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
SUBTLE_FONT = Font(italic=True, color="555555")
THIN = Side(style="thin", color="D0D5DD")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
STATUS_CODE = {"present": "P", "late": "L", "absent": "A"}
STATUS_FILL = {
    "L": PatternFill("solid", fgColor="FDECC8"),
    "A": PatternFill("solid", fgColor="FBD5D9"),
}


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------
def _local(dt):
    return timezone.localtime(dt).replace(tzinfo=None) if dt else None


def _title_block(ws, title, lines):
    """School name, report title and information lines above the table. Returns the header row number."""
    conf = settings.SMARTATTENDANCE
    ws.append([conf["SCHOOL_NAME"]])
    ws["A1"].font = Font(bold=True, size=11, color=BRAND)
    ws.append([title])
    ws["A2"].font = TITLE_FONT
    for line in lines:
        ws.append([line])
        ws.cell(row=ws.max_row, column=1).font = SUBTLE_FONT
    ws.append([])
    return ws.max_row + 1


def _table(ws, header_row, headers, rows, widths=None, number_formats=None):
    for col, text in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col, value=text)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BOX
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r, values in enumerate(rows, start=header_row + 1):
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=col, value=value)
            cell.border = BOX
            fmt = (number_formats or {}).get(col)
            if fmt:
                cell.number_format = fmt
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    # Print-friendly: landscape, all columns on one page width, header row repeated on every page.
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.print_title_rows = f"{header_row}:{header_row}"
    if rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{header_row + len(rows)}"
    for col, text in enumerate(headers, start=1):
        if widths and col <= len(widths) and widths[col - 1]:
            width = widths[col - 1]
        else:
            longest = max([len(str(text))] + [len(str(r[col - 1])) for r in rows if r[col - 1] is not None] or [8])
            width = min(max(longest + 2, 8), 45)
        ws.column_dimensions[get_column_letter(col)].width = width


def _to_bytes(wb):
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _profile(s):
    p = getattr(s, "student_profile", None)
    return (p.course_program, p.year_level) if p else ("", "")


# ---------------------------------------------------------------------------
# Class record of one subject
# ---------------------------------------------------------------------------
def build_subject_workbook(subject):
    """Three sheets: Summary (ranked), Attendance grid (P/L/A per session) and Sessions."""
    generated = timezone.localtime()
    students = User.objects.filter(enrolled_subjects__subject=subject).select_related("student_profile")
    logs = AttendanceLog.objects.filter(session__subject=subject)
    rows = rank_by_attendance(student_rows_with_standing(students, logs))       # merge sort + ranking
    sessions = merge_sort(ClassSession.objects.filter(subject=subject), key=lambda s: s.created_at)
    info = [f"Subject: {subject.subject_code} – {subject.title}",
            f"Instructor: {full_name(subject.teacher)}",
            f"Generated: {generated:%B %d, %Y %I:%M %p} · {len(rows)} students · {len(sessions)} sessions"]

    wb = Workbook()
    # --- Summary -----------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    header_row = _title_block(ws, "Class Attendance Summary", info)
    _table(ws, header_row,
           ["Rank", "Student ID", "Last name", "First name", "Program", "Year", "Present", "Late", "Absent",
            "Total", "Attendance rate", "Standing"],
           [(r.rank, r.id_number, r.last_name, r.first_name, *_profile(r), r.n_present, r.n_late, r.n_absent,
             r.n_total, r.attendance_rate / 100, r.standing) for r in rows],
           number_formats={11: "0.0%"})

    # --- Attendance grid -------------------------------------------------------
    ws = wb.create_sheet("Attendance grid")
    header_row = _title_block(ws, "Attendance Record (P = present, L = late, A = absent, – = no record)", info)
    status_of = {(sid, ses): st for sid, ses, st in logs.values_list("student_id", "session_id", "status")}
    by_name = merge_sort(rows, key=lambda r: ((r.last_name or r.username).casefold(), (r.first_name or "").casefold()))
    headers = ["Student ID", "Name"] + [f"{_local(s.created_at):%b %d}\n{_local(s.created_at):%I:%M %p}"
                                         for s in sessions] + ["P", "L", "A"]
    grid = []
    for r in by_name:
        marks = [STATUS_CODE.get(status_of.get((r.pk, s.pk)), "–") for s in sessions]
        grid.append([r.id_number, full_name(r), *marks, r.n_present, r.n_late, r.n_absent])
    _table(ws, header_row, headers, grid, widths=[12, 28] + [9] * len(sessions) + [5, 5, 5])
    ws.row_dimensions[header_row].height = 32
    for row in ws.iter_rows(min_row=header_row + 1, min_col=3, max_col=2 + len(sessions)):
        for cell in row:
            cell.alignment = Alignment(horizontal="center")
            if cell.value in STATUS_FILL:
                cell.fill = STATUS_FILL[cell.value]
    ws.freeze_panes = ws.cell(row=header_row + 1, column=3)

    # --- Sessions ------------------------------------------------------------------
    ws = wb.create_sheet("Sessions")
    header_row = _title_block(ws, "Class Sessions", info)
    tallies = count_by_group(logs.values_list("session_id", "status"), initial=STATUSES)
    empty = {k: 0 for k in STATUSES}
    _table(ws, header_row,
           ["No.", "Date", "Started", "Title", "Late after (min)", "Geofence", "Present", "Late", "Absent", "Status"],
           [(i, s.date, _local(s.created_at), s.session_title, s.late_after_minutes,
             f"{s.allowed_radius_meters} m" if s.has_geofence else "Off",
             tallies.get(s.pk, empty)["present"], tallies.get(s.pk, empty)["late"], tallies.get(s.pk, empty)["absent"],
             "Open" if s.is_open else "Closed") for i, s in enumerate(sessions, start=1)],
           widths=[6, 12, 10, 34, 10, 10, 9, 7, 8, 9],
           number_formats={2: "yyyy-mm-dd", 3: "hh:mm AM/PM"})
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# Institution-wide report
# ---------------------------------------------------------------------------
def build_school_workbook(date_from=None, date_to=None, department=None):
    """Three sheets: Subjects, Students (needs-attention first) and Audit log, for an optional date range
    (by class date) and department."""
    generated = timezone.localtime()
    logs = AttendanceLog.objects.all()
    sessions = ClassSession.objects.all()
    audits = AuditScanLog.objects.select_related("student", "student__student_profile", "session__subject")
    subjects = Subject.objects.select_related("teacher", "department")
    if date_from:
        logs, sessions = logs.filter(session__date__gte=date_from), sessions.filter(date__gte=date_from)
        audits = audits.filter(created_at__date__gte=date_from)
    if date_to:
        logs, sessions = logs.filter(session__date__lte=date_to), sessions.filter(date__lte=date_to)
        audits = audits.filter(created_at__date__lte=date_to)
    if department:
        logs = logs.filter(session__subject__department=department)
        sessions = sessions.filter(subject__department=department)
        audits = audits.filter(session__subject__department=department)
        subjects = subjects.filter(department=department)

    period = (f"{date_from or 'the first record'} to {date_to or 'today'}"
              if (date_from or date_to) else "all dates")
    info = [f"Period: {period} · Department: {department.code + ' – ' + department.name if department else 'all'}",
            f"Generated: {generated:%B %d, %Y %I:%M %p}"]

    wb = Workbook()
    # --- Subjects ------------------------------------------------------------------
    ws = wb.active
    ws.title = "Subjects"
    header_row = _title_block(ws, "Attendance by Subject", info)
    subject_list = merge_sort(subjects, key=lambda s: s.subject_code.casefold())
    tallies = count_by_group(logs.values_list("session__subject_id", "status"), initial=STATUSES)
    enrolled = count_by_group((e, "n") for e in SubjectEnrollment.objects.values_list("subject_id", flat=True))
    held = count_by_group((s, "n") for s in sessions.values_list("subject_id", flat=True))
    rows = []
    for s in subject_list:
        summ = summary_from_counts(tallies.get(s.pk, {}))
        rows.append((s.subject_code, s.title, s.department.code if s.department else "", full_name(s.teacher),
                     enrolled.get(s.pk, {}).get("n", 0), held.get(s.pk, {}).get("n", 0),
                     summ["present"], summ["late"], summ["absent"], summ["attendance_rate"] / 100 if summ["total"] else None))
    _table(ws, header_row, ["Code", "Title", "Department", "Instructor", "Enrolled", "Sessions", "Present", "Late",
                            "Absent", "Attendance rate"], rows, number_formats={10: "0.0%"})

    # --- Students ------------------------------------------------------------------
    ws = wb.create_sheet("Students")
    header_row = _title_block(ws, "Student Standing (needs attention first)", info)
    students = User.objects.filter(role=User.Role.STUDENT, is_active=True).select_related("student_profile")
    if department:
        students = students.filter(enrolled_subjects__subject__department=department).distinct()
    # Merge sort: students needing attention first (critical, then frequent tardy, lowest rate first);
    # students without any record in the period go last.
    rows = merge_sort(student_rows_with_standing(students, logs),
                      key=lambda r: (r.n_total == 0, STANDING_ORDER.get(r.standing, 3), r.attendance_rate,
                                     (r.last_name or r.username).casefold(), (r.first_name or "").casefold()))
    _table(ws, header_row,
           ["Student ID", "Last name", "First name", "Program", "Year", "Account", "Present", "Late", "Absent",
            "Total", "Attendance rate", "Standing"],
           [(r.id_number, r.last_name, r.first_name, *_profile(r), r.status, r.n_present, r.n_late, r.n_absent,
             r.n_total, r.attendance_rate / 100 if r.n_total else None, r.standing if r.n_total else "No records")
            for r in rows],
           number_formats={11: "0.0%"})

    # --- Audit log -------------------------------------------------------------------
    ws = wb.create_sheet("Audit log")
    header_row = _title_block(ws, "Rejected Scans", info)
    reason_label = dict(AuditScanLog.Reason.choices)
    _table(ws, header_row,
           ["Date and time", "Reason", "Student", "Student ID", "Subject", "Device ID", "Latitude", "Longitude"],
           [(_local(a.created_at), reason_label.get(a.reason, a.reason),
             full_name(a.student) if a.student else "Unknown", a.student.id_number if a.student else "",
             a.session.subject.subject_code if a.session else "", a.submitted_device_id or "",
             float(a.student_latitude) if a.student_latitude is not None else None,
             float(a.student_longitude) if a.student_longitude is not None else None)
            for a in audits.order_by("created_at")],
           widths=[20, 24, 26, 12, 10, 38, 11, 11],
           number_formats={1: "yyyy-mm-dd hh:mm AM/PM", 7: "0.000000", 8: "0.000000"})
    return _to_bytes(wb)
