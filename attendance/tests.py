"""
Automated checks for the anti-proxy pipeline, RBAC, soft-delete rules and every page.

    python manage.py test
"""
import io
from datetime import timedelta
from decimal import Decimal

import jwt
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
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
from .services import haversine_meters
from .tokens import issue_qr_token

LAT, LNG = Decimal("7.825700"), Decimal("123.437000")
PASSWORD = "Str0ng!Passw0rd"
Reason = AuditScanLog.Reason


def make_user(username, role, status=User.AccountStatus.ACTIVE, **kw):
    u = User.objects.create_user(username=username, email=f"{username}@test.ph", password=PASSWORD,
                                 role=role, status=status, **kw)
    if role == User.Role.STUDENT:
        StudentProfile.objects.create(user=u, student_id_number=f"2024-{u.pk:04d}", course_program="BSIS")
    return u


class BaseCase(TestCase):
    def setUp(self):
        self.admin = make_user("admin", User.Role.SUPERADMIN, is_staff=True, is_superuser=True)
        self.teacher = make_user("teacher", User.Role.INSTRUCTOR)
        self.other_teacher = make_user("teacher2", User.Role.INSTRUCTOR)
        self.student = make_user("student", User.Role.STUDENT)
        self.subject = Subject.objects.create(subject_code="IS201", title="Systems Analysis", teacher=self.teacher)
        SubjectEnrollment.objects.create(subject=self.subject, student=self.student)
        self.session = self.new_session()

    def new_session(self, **kw):
        defaults = dict(subject=self.subject, expires_at=timezone.now() + timedelta(hours=1),
                        instructor_latitude=LAT, instructor_longitude=LNG, allowed_radius_meters=40)
        defaults.update(kw)
        return ClassSession.objects.create(**defaults)

    def scan(self, user=None, session=None, device="dev-A", lat=7.8257, lng=123.4370, token=None):
        self.client.force_login(user or self.student)
        token = token or issue_qr_token(session or self.session)[0]
        return self.client.post(reverse("api_scan"), {"token": token, "latitude": lat, "longitude": lng,
                                                      "device_id": device}, content_type="application/json")


class GeoTests(TestCase):
    def test_haversine(self):
        self.assertAlmostEqual(haversine_meters(0, 0, 0, 0), 0, places=3)
        # one degree of latitude ≈ 111.19 km
        self.assertAlmostEqual(haversine_meters(7, 123, 8, 123) / 1000, 111.19, delta=0.2)


class ScanPipelineTests(BaseCase):
    def test_success_binds_device_and_marks_present(self):
        r = self.scan()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "present")
        self.assertTrue(r.json()["device_bound_now"])
        self.student.refresh_from_db()
        self.assertEqual(self.student.device_id, "dev-A")
        self.assertEqual(AttendanceLog.objects.get().status, "present")

    def test_duplicate_scan_is_idempotent(self):
        self.scan()
        r = self.scan()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AttendanceLog.objects.count(), 1)

    def test_late_after_15_minutes(self):
        ClassSession.objects.filter(pk=self.session.pk).update(created_at=timezone.now() - timedelta(minutes=16))
        self.session.refresh_from_db()
        self.assertEqual(self.scan().json()["status"], "late")

    def test_tampered_token_logged_as_expired_token(self):
        bad = jwt.encode({"session_id": self.session.pk, "iat": 1, "exp": 9999999999}, "wrong-secret", "HS256")
        r = self.scan(token=bad)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.EXPIRED_TOKEN)
        self.assertFalse(AttendanceLog.objects.exists())

    def test_expired_token_keeps_session_link(self):
        conf = settings.SMARTATTENDANCE
        old = jwt.encode({"session_id": self.session.pk, "iat": 1, "exp": int(timezone.now().timestamp()) - 60},
                         conf["QR_JWT_SECRET"], conf["QR_JWT_ALGORITHM"])
        r = self.scan(token=old)
        self.assertEqual(r.status_code, 400)
        log = AuditScanLog.objects.get()
        self.assertEqual((log.reason, log.session_id), (Reason.EXPIRED_TOKEN, self.session.pk))

    def test_closed_session_rejected(self):
        token = issue_qr_token(self.session)[0]
        ClassSession.objects.filter(pk=self.session.pk).update(is_active=False)
        r = self.scan(token=token)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.SESSION_CLOSED)

    def test_device_mismatch_403(self):
        self.assertEqual(self.scan(device="dev-A").status_code, 201)
        other = self.new_session()
        r = self.scan(device="dev-B", session=other)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["message"], "Device mismatch. Bound to another phone.")
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.DEVICE_MISMATCH)
        self.assertEqual(AttendanceLog.objects.filter(session=other).count(), 0)

    def test_phone_of_another_student_is_refused(self):
        """One phone per student: a classmate cannot sign in on a bound phone and check in."""
        self.assertEqual(self.scan(device="dev-A").status_code, 201)
        classmate = make_user("classmate", User.Role.STUDENT)
        SubjectEnrollment.objects.create(subject=self.subject, student=classmate)
        r = self.scan(user=classmate, device="dev-A")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "device_in_use")
        classmate.refresh_from_db()
        self.assertIsNone(classmate.device_id)
        self.assertFalse(AttendanceLog.objects.filter(student=classmate).exists())
        audit = AuditScanLog.objects.get()
        self.assertEqual((audit.reason, audit.student, audit.submitted_device_id),
                         (Reason.DEVICE_MISMATCH, classmate, "dev-A"))

    def test_phone_can_change_hands_after_unbinding(self):
        self.assertEqual(self.scan(device="dev-A").status_code, 201)
        User.objects.filter(pk=self.student.pk).update(device_id=None)   # instructor pressed Unbind Device
        classmate = make_user("classmate", User.Role.STUDENT)
        SubjectEnrollment.objects.create(subject=self.subject, student=classmate)
        r = self.scan(user=classmate, device="dev-A")
        self.assertEqual(r.status_code, 201, r.content)
        classmate.refresh_from_db()
        self.assertEqual(classmate.device_id, "dev-A")

    def test_database_refuses_two_accounts_on_one_phone(self):
        User.objects.filter(pk=self.student.pk).update(device_id="dev-A")
        classmate = make_user("classmate", User.Role.STUDENT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=classmate.pk).update(device_id="dev-A")
        # unbound accounts (NULL) are not affected by the rule
        User.objects.filter(pk__in=[self.teacher.pk, self.other_teacher.pk]).update(device_id=None)

    def test_out_of_bounds_403(self):
        r = self.scan(lat=7.8302, lng=123.4370)  # ≈ 500 m north
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["message"], "Out of classroom boundaries.")
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.OUT_OF_BOUNDS)
        self.assertFalse(AttendanceLog.objects.exists())

    def test_missing_gps_rejected_when_geofenced(self):
        r = self.scan(lat=None, lng=None)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.OUT_OF_BOUNDS)

    def test_unenrolled_student_audited_not_logged(self):
        stranger = make_user("stranger", User.Role.STUDENT, status=User.AccountStatus.PENDING)
        r = self.scan(user=stranger)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["message"], "Not enrolled in this subject.")
        self.assertFalse(AttendanceLog.objects.filter(student=stranger).exists())
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.UNENROLLED_STUDENT)

    def test_suspended_student_cannot_scan(self):
        User.objects.filter(pk=self.student.pk).update(status=User.AccountStatus.SUSPENDED)
        self.assertEqual(self.scan().status_code, 403)
        self.assertFalse(AttendanceLog.objects.exists())

    def test_non_student_cannot_scan(self):
        self.assertEqual(self.scan(user=self.teacher).status_code, 403)

    def test_session_without_gps_skips_geofence(self):
        s = self.new_session(instructor_latitude=None, instructor_longitude=None)
        self.assertEqual(self.scan(session=s, lat=None, lng=None).status_code, 201)


class InstructorActionTests(BaseCase):
    def test_rogue_scan_approve_enrolls_promotes_and_logs(self):
        stranger = make_user("stranger", User.Role.STUDENT, status=User.AccountStatus.PENDING)
        self.scan(user=stranger)
        audit = AuditScanLog.objects.get()

        self.client.force_login(self.teacher)
        live = self.client.get(reverse("api_session_live", args=[self.session.pk])).json()
        self.assertEqual([r["audit_id"] for r in live["rogue"]], [audit.pk])

        r = self.client.post(reverse("api_rogue_approve", args=[self.session.pk, audit.pk]))
        self.assertEqual(r.status_code, 200, r.content)
        stranger.refresh_from_db()
        self.assertEqual(stranger.status, User.AccountStatus.ACTIVE)
        self.assertTrue(SubjectEnrollment.objects.filter(subject=self.subject, student=stranger).exists())
        self.assertTrue(AttendanceLog.objects.filter(session=self.session, student=stranger).exists())
        live = self.client.get(reverse("api_session_live", args=[self.session.pk])).json()
        self.assertEqual(live["rogue"], [])

    def test_enrolling_pending_student_promotes_to_active(self):
        pending = make_user("pend", User.Role.STUDENT, status=User.AccountStatus.PENDING)
        self.client.force_login(self.teacher)
        r = self.client.post(reverse("api_enroll", args=[self.subject.pk]), {"identifier": pending.student_profile.student_id_number},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        pending.refresh_from_db()
        self.assertEqual(pending.status, User.AccountStatus.ACTIVE)

    def test_drop_removes_enrollment_only(self):
        self.scan()
        self.client.force_login(self.teacher)
        r = self.client.post(reverse("api_drop", args=[self.subject.pk, self.student.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SubjectEnrollment.objects.exists())
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())
        self.assertEqual(AttendanceLog.objects.count(), 1)  # history preserved

    def test_unbind_device(self):
        self.scan()
        self.client.force_login(self.teacher)
        self.assertEqual(self.client.post(reverse("api_unbind_device", args=[self.student.pk])).status_code, 200)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.device_id)

    def test_inline_status_and_notes_update(self):
        self.client.force_login(self.teacher)
        r = self.client.post(reverse("api_attendance_update", args=[self.session.pk]),
                             {"student_id": self.student.pk, "status": "late", "notes": "Medical excuse verified"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        log = AttendanceLog.objects.get()
        self.assertEqual((log.status, log.notes), ("late", "Medical excuse verified"))

    def test_student_profile_modal_data(self):
        self.scan()
        self.client.force_login(self.teacher)
        d = self.client.get(reverse("api_student_profile", args=[self.subject.pk, self.student.pk])).json()
        self.assertEqual(d["summary"]["standing"], "Good Standing")
        self.assertEqual(d["summary"]["attendance_rate"], 100.0)

    def test_closing_session_marks_unscanned_absent(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("api_session_close", args=[self.session.pk]))
        self.assertEqual(AttendanceLog.objects.get().status, "absent")

    def test_qr_token_endpoint_and_ownership(self):
        self.client.force_login(self.teacher)
        r = self.client.get(reverse("api_qr_token", args=[self.session.pk]))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("<svg", data["svg"])
        claims = jwt.decode(data["token"], settings.SMARTATTENDANCE["QR_JWT_SECRET"], algorithms=["HS256"])
        self.assertEqual(claims["session_id"], self.session.pk)
        self.assertEqual(claims["exp"] - claims["iat"], 10)
        # Another instructor cannot see it, nor can a student.
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(reverse("api_qr_token", args=[self.session.pk])).status_code, 404)
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("api_qr_token", args=[self.session.pk])).status_code, 403)

    def start(self, **over):
        body = {"subject_id": self.subject.pk, "duration_minutes": 30, "allowed_radius_meters": 40}
        body.update(over)
        self.client.force_login(self.teacher)
        return self.client.post(reverse("api_session_start"), body, content_type="application/json")

    def test_start_session_api(self):
        r = self.start(latitude=7.8257, longitude=123.437)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(ClassSession.objects.get(pk=r.json()["session"]["id"]).has_geofence)

    def test_geofence_on_requires_instructor_location(self):
        for extra in ({}, {"geofence_enabled": True}, {"geofence_enabled": True, "latitude": 7.8, "longitude": None}):
            r = self.start(**extra)
            self.assertEqual(r.status_code, 400, extra)
        self.assertEqual(ClassSession.objects.filter(subject=self.subject).count(), 1)  # only the setUp session

    def test_geofence_off_session_stores_no_coordinates(self):
        # coordinates sent by a client are ignored when the instructor turned geofencing off
        r = self.start(geofence_enabled=False, latitude=7.8257, longitude=123.437)
        self.assertEqual(r.status_code, 201, r.content)
        s = ClassSession.objects.get(pk=r.json()["session"]["id"])
        self.assertFalse(s.has_geofence)
        self.assertIsNone(s.instructor_latitude)
        self.assertFalse(r.json()["session"]["has_geofence"])
        # ...and no location is needed at all
        self.assertEqual(self.start(geofence_enabled=False).status_code, 201)

    def test_online_session_accepts_scan_from_anywhere_but_keeps_other_locks(self):
        online = ClassSession.objects.get(pk=self.start(geofence_enabled=False).json()["session"]["id"])
        # far away, and with no GPS at all
        r = self.scan(session=online, lat=14.5995, lng=120.9842)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(AuditScanLog.objects.exists())
        other = make_user("remote", User.Role.STUDENT)
        SubjectEnrollment.objects.create(subject=self.subject, student=other)
        self.assertEqual(self.scan(user=other, session=online, lat=None, lng=None, device="dev-R").status_code, 201)
        # device lock still enforced
        self.assertEqual(self.scan(user=other, session=online, device="dev-OTHER").status_code, 403)
        self.assertEqual(AuditScanLog.objects.get().reason, Reason.DEVICE_MISMATCH)
        # unenrolled students are still caught
        stranger = make_user("stranger", User.Role.STUDENT, status=User.AccountStatus.PENDING)
        self.assertEqual(self.scan(user=stranger, session=online).status_code, 403)

    def test_live_page_exposes_poll_interval_and_geofence_state(self):
        self.client.force_login(self.teacher)
        r = self.client.get(reverse("instructor_session", args=[self.session.pk]))
        self.assertContains(r, 'data-live-poll="30"')
        self.assertContains(r, 'data-refresh="8"')
        self.assertContains(r, "40 m geofence")
        online = self.new_session(instructor_latitude=None, instructor_longitude=None)
        self.assertContains(self.client.get(reverse("instructor_session", args=[online.pk])), "Geofencing off")

    def test_late_time_is_chosen_per_session(self):
        r = self.start(geofence_enabled=False, late_after_minutes=10)
        self.assertEqual(r.status_code, 201, r.content)
        session = ClassSession.objects.get(pk=r.json()["session"]["id"])
        self.assertEqual((session.late_after_minutes, r.json()["session"]["late_after_minutes"]), (10, 10))
        ClassSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(minutes=11))
        session.refresh_from_db()
        self.assertEqual(self.scan(session=session).json()["status"], "late")     # 11 min > 10 min
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("instructor_session", args=[session.pk])), "Late after 10 min")

    def test_custom_late_time_and_default(self):
        r = self.start(geofence_enabled=False, late_after_minutes=20)
        self.assertEqual(ClassSession.objects.get(pk=r.json()["session"]["id"]).late_after_minutes, 20)
        r = self.start(geofence_enabled=False)                                       # not sent -> default 15
        session = ClassSession.objects.get(pk=r.json()["session"]["id"])
        self.assertEqual(session.late_after_minutes, 15)
        ClassSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(minutes=11))
        session.refresh_from_db()
        self.assertEqual(self.scan(session=session).json()["status"], "present")  # 11 min <= 15 min

    def test_invalid_late_times_rejected(self):
        for late in (0, 181, -5):
            self.assertEqual(self.start(geofence_enabled=False, late_after_minutes=late).status_code, 400)
        r = self.start(geofence_enabled=False, duration_minutes=30, late_after_minutes=30)  # not shorter
        self.assertEqual(r.status_code, 400)
        self.assertIn("shorter than the session duration", r.content.decode())


class AccountLifecycleTests(TestCase):
    def register(self, **over):
        data = {"role": "student", "username": "newbie", "first_name": "New", "last_name": "Bie",
                "email": "newbie@test.ph", "password1": PASSWORD, "password2": PASSWORD,
                "student_id_number": "2025-9999", "course_program": "BSIS", "year_level": 1,
                "city_municipality": "Pagadian City", "province": "Zamboanga del Sur", "barangay": "Gatas"}
        data.update(over)
        return self.client.post(reverse("register"), data)

    def test_student_signup_is_pending_and_sees_banner(self):
        self.assertEqual(self.register().status_code, 302)
        u = User.objects.get(username="newbie")
        self.assertEqual((u.role, u.status), ("student", "pending"))
        self.assertEqual(u.address.province, "Zamboanga del Sur")
        r = self.client.get(reverse("student_dashboard"))
        self.assertContains(r, "Account Pending Verification: Awaiting enrollment from your instructor.")

    def test_instructor_signup_pending_blocked_until_approved(self):
        self.register(role="instructor", username="prof", email="prof@test.ph", student_id_number="", course_program="")
        prof = User.objects.get(username="prof")
        self.assertEqual(prof.status, "pending")
        self.assertRedirects(self.client.get(reverse("instructor_dashboard")), reverse("pending_approval"))
        self.assertEqual(self.client.post(reverse("api_session_start"), {}, content_type="application/json").status_code, 403)

        admin = make_user("boss", User.Role.SUPERADMIN)
        self.client.force_login(admin)
        self.client.post(reverse("admin_user_action", args=[prof.pk]), {"action": "approve"})
        prof.refresh_from_db()
        self.assertEqual(prof.status, "active")
        self.client.force_login(prof)
        self.assertEqual(self.client.get(reverse("instructor_dashboard")).status_code, 200)

    def test_suspended_user_cannot_log_in(self):
        make_user("sus", User.Role.STUDENT, status=User.AccountStatus.SUSPENDED)
        r = self.client.post(reverse("login"), {"username": "sus", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "suspended")

    def test_login_by_email_and_archived_cannot_login(self):
        make_user("mail", User.Role.STUDENT)
        self.assertEqual(self.client.post(reverse("login"), {"username": "mail@test.ph", "password": PASSWORD}).status_code, 302)
        self.client.logout()
        make_user("gone", User.Role.STUDENT, is_active=False)
        r = self.client.post(reverse("login"), {"username": "gone", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)

    def test_archive_is_soft_delete(self):
        admin = make_user("boss", User.Role.SUPERADMIN)
        stu = make_user("stu", User.Role.STUDENT)
        self.client.force_login(admin)
        self.client.post(reverse("admin_user_action", args=[stu.pk]), {"action": "archive"})
        stu.refresh_from_db()
        self.assertFalse(stu.is_active)
        self.assertTrue(User.objects.filter(pk=stu.pk).exists())
        self.client.post(reverse("admin_user_action", args=[stu.pk]), {"action": "restore"})
        stu.refresh_from_db()
        self.assertTrue(stu.is_active)

    def test_instructor_cannot_open_admin_pages(self):
        self.client.force_login(make_user("t", User.Role.INSTRUCTOR))
        for name in ("admin_dashboard", "admin_users", "admin_subjects", "admin_audit"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_student_cannot_open_instructor_pages(self):
        self.client.force_login(make_user("s", User.Role.STUDENT))
        self.assertEqual(self.client.get(reverse("instructor_dashboard")).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        r = self.client.get(reverse("student_dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("login"), r["Location"])


class PageSmokeTests(TestCase):
    """Every page renders for the right role, using the real seed data."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", stdout=io.StringIO())

    def get_ok(self, name, *args, **params):
        r = self.client.get(reverse(name, args=args), params)
        self.assertEqual(r.status_code, 200, f"{name} -> {r.status_code}")
        return r

    def test_public_pages(self):
        self.get_ok("landing")
        r = self.get_ok("login")
        self.assertContains(r, "images/zdspgc_logo.png")
        self.get_ok("register")

    def test_superadmin_pages(self):
        self.client.force_login(User.objects.get(email="admin@school.edu.ph"))
        r = self.get_ok("admin_dashboard")
        self.assertContains(r, "Maria Reyes")           # pending instructor
        self.assertContains(r, "Critical Absences")     # truancy watchlist
        self.get_ok("admin_users")
        self.get_ok("admin_users", role="student", status="pending", q="Garcia")
        self.get_ok("admin_users", status="archived")
        self.get_ok("admin_subjects")
        r = self.get_ok("admin_audit")
        self.assertContains(r, "Device mismatch")
        self.get_ok("admin_audit", reason="out_of_bounds", q="Mendoza")
        for u in User.objects.all():
            self.get_ok("admin_user_edit", u.pk)

    def test_admin_can_edit_user_without_address(self):
        admin = User.objects.get(email="admin@school.edu.ph")
        Address.objects.filter(user=admin).delete()
        self.client.force_login(admin)
        r = self.client.post(reverse("admin_user_edit", args=[admin.pk]), {
            "u-first_name": "Sys", "u-last_name": "Admin", "u-email": admin.email, "u-username": admin.username,
            "u-role": "admin", "u-status": "active",
            "a-street_address": "", "a-barangay": "", "a-city_municipality": "", "a-province": "", "a-postal_code": ""})
        self.assertEqual(r.status_code, 302, r.content[:400])
        admin.refresh_from_db()
        self.assertEqual(admin.first_name, "Sys")

    def test_admin_subject_management(self):
        self.client.force_login(User.objects.get(email="admin@school.edu.ph"))
        boss = User.objects.get(username="jdelacruz")
        dept = Department.objects.get(code="CCS")
        r = self.client.post(reverse("admin_subjects"), {"form": "subject", "s-subject_code": "IT300", "s-title": "Networks",
                                                         "s-teacher": boss.pk, "s-department": dept.pk})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Subject.objects.filter(subject_code="IT300").exists())
        r = self.client.post(reverse("admin_subjects"), {"form": "department", "d-code": "BA", "d-name": "Business"})
        self.assertEqual(r.status_code, 302)
        # a pending instructor cannot be assigned
        pending = User.objects.get(username="mreyes")
        self.client.post(reverse("admin_subjects"), {"form": "reassign", "subject_id": Subject.objects.get(subject_code="IT300").pk,
                                                     "teacher_id": pending.pk})
        self.assertEqual(Subject.objects.get(subject_code="IT300").teacher, boss)

    def test_instructor_pages(self):
        self.client.force_login(User.objects.get(username="jdelacruz"))
        r = self.get_ok("instructor_dashboard")
        self.assertContains(r, "IS201")
        subj = Subject.objects.get(subject_code="IS201")
        self.get_ok("instructor_subject", subj.pk)
        sess = ClassSession.objects.filter(subject=subj).first()
        self.get_ok("instructor_session", sess.pk)
        r = self.get_ok("instructor_session_export", sess.pk)
        self.assertEqual(r["Content-Type"], "text/csv")

    def test_pending_instructor_redirected(self):
        self.client.force_login(User.objects.get(username="mreyes"))
        self.assertRedirects(self.client.get(reverse("instructor_dashboard")), reverse("pending_approval"))
        self.get_ok("pending_approval")

    def test_student_pages_and_history_filter(self):
        self.client.force_login(User.objects.get(username="asantos"))
        r = self.get_ok("student_dashboard")
        self.assertContains(r, "Total Attended")
        subj = Subject.objects.get(subject_code="CS101")
        self.get_ok("student_dashboard", subject=subj.pk)
        self.get_ok("student_scan")

    def test_pending_student_banner_and_unbound_student(self):
        self.client.force_login(User.objects.get(username="egarcia"))
        self.assertContains(self.get_ok("student_dashboard"), "Account Pending Verification")
        self.assertIsNone(User.objects.get(username="dvillanueva").device_id)

    def test_seed_is_idempotent_and_matches_spec(self):
        before = (User.objects.count(), AttendanceLog.objects.count(), AuditScanLog.objects.count())
        call_command("seed_data", stdout=io.StringIO())
        self.assertEqual(before, (User.objects.count(), AttendanceLog.objects.count(), AuditScanLog.objects.count()))
        self.assertEqual(User.objects.filter(role="admin").count(), 1)
        self.assertEqual(User.objects.filter(role="instructor", status="active").count(), 1)
        self.assertEqual(User.objects.filter(role="instructor", status="pending").count(), 1)
        self.assertEqual(User.objects.filter(role="student").count(), 5)
        self.assertEqual(Subject.objects.count(), 2)
