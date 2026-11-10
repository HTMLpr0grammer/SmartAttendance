"""Tests for the Excel exports (attendance/exports.py) and their download views."""
import io
from datetime import date

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from .exports import XLSX_CONTENT_TYPE
from .models import AttendanceLog, AuditScanLog, ClassSession, Department, Subject, User


class ExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", stdout=io.StringIO())
        cls.teacher = User.objects.get(username="jdelacruz")
        cls.admin = User.objects.get(username="admin")
        cls.subject = Subject.objects.get(subject_code="IS201")

    def download(self, user, url, **params):
        self.client.force_login(user)
        r = self.client.get(url, params)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r["Content-Type"], XLSX_CONTENT_TYPE)
        self.assertIn(".xlsx", r["Content-Disposition"])
        return load_workbook(io.BytesIO(r.content))

    @staticmethod
    def table(ws):
        """Rows under the header row (the row whose first cell is a column title in the brand colour)."""
        rows = list(ws.iter_rows(values_only=True))
        start = next(i for i, row in enumerate(rows) if row[0] in ("Rank", "Student ID", "No.", "Code", "Date and time"))
        return rows[start], [r for r in rows[start + 1:] if any(v is not None for v in r)]

    def test_instructor_class_record(self):
        wb = self.download(self.teacher, reverse("instructor_subject_export_xlsx", args=[self.subject.pk]))
        self.assertEqual(wb.sheetnames, ["Summary", "Attendance grid", "Sessions"])

        header, rows = self.table(wb["Summary"])
        self.assertEqual(header[:3], ("Rank", "Student ID", "Last name"))
        self.assertEqual(len(rows), self.subject.enrollments.count())
        rates = [r[10] for r in rows]
        self.assertEqual(rates, sorted(rates, reverse=True))            # ranked by attendance rate
        self.assertEqual(rows[0][0], 1)
        logs = AttendanceLog.objects.filter(session__subject=self.subject)
        self.assertEqual(sum(r[6] + r[7] + r[8] for r in rows), logs.count())   # counts match the database

        header, grid = self.table(wb["Attendance grid"])
        sessions = ClassSession.objects.filter(subject=self.subject).count()
        self.assertEqual(len(header), 2 + sessions + 3)
        marks = {v for r in grid for v in r[2:2 + sessions]}
        self.assertTrue(marks <= {"P", "L", "A", "–"})
        for r in grid:                                                     # P/L/A totals agree with the marks
            cells = r[2:2 + sessions]
            self.assertEqual((cells.count("P"), cells.count("L"), cells.count("A")), tuple(r[-3:]))

        header, srows = self.table(wb["Sessions"])
        self.assertEqual(len(srows), sessions)
        self.assertIn("Late after (min)", header)

    def test_instructor_cannot_export_another_teachers_subject(self):
        other = User.objects.create_user("t2", "t2@x.ph", "x", role="instructor", status="active")
        theirs = Subject.objects.create(subject_code="ZZ999", title="Other", teacher=other)
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.get(reverse("instructor_subject_export_xlsx", args=[theirs.pk])).status_code, 404)
        self.client.force_login(User.objects.get(username="asantos"))
        self.assertEqual(self.client.get(reverse("admin_export_xlsx")).status_code, 403)

    def test_superadmin_school_report(self):
        wb = self.download(self.admin, reverse("admin_export_xlsx"))
        self.assertEqual(wb.sheetnames, ["Subjects", "Students", "Audit log"])
        _, subjects = self.table(wb["Subjects"])
        self.assertEqual([r[0] for r in subjects], sorted(s.subject_code for s in Subject.objects.all()))
        _, students = self.table(wb["Students"])
        self.assertEqual(len(students), User.objects.filter(role="student", is_active=True).count())
        _, audit = self.table(wb["Audit log"])
        self.assertEqual(len(audit), AuditScanLog.objects.count())

    def test_superadmin_filters_and_subject_record(self):
        dept = Department.objects.first()
        wb = self.download(self.admin, reverse("admin_export_xlsx"), department=dept.pk, **{"from": "2000-01-01",
                                                                                          "to": date.today().isoformat()})
        _, subjects = self.table(wb["Subjects"])
        self.assertTrue(all(r[2] == dept.code for r in subjects))
        wb = self.download(self.admin, reverse("admin_subject_export_xlsx", args=[self.subject.pk]))
        self.assertEqual(wb.sheetnames, ["Summary", "Attendance grid", "Sessions"])

    def test_bad_dates_return_to_dashboard_with_message(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("admin_export_xlsx"), {"from": "2026-13-40"}, follow=True)
        self.assertContains(r, "is not a valid date")
        r = self.client.get(reverse("admin_export_xlsx"), {"from": "2026-10-20", "to": "2026-10-01"}, follow=True)
        self.assertContains(r, "start date must be on or before the end date")

    def test_dashboards_show_download_buttons(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("instructor_dashboard")), "Download Excel class record")
        self.assertContains(self.client.get(reverse("instructor_subject", args=[self.subject.pk])), "Excel class record")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("admin_dashboard")), "Download Excel")
        self.assertContains(self.client.get(reverse("admin_subjects")), "Excel class record")
