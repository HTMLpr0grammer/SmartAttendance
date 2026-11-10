from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import admin_panel, instructor, public, student

urlpatterns = [
    # Public / auth
    path("", public.landing, name="landing"),
    path("login/", public.SmartLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", public.register, name="register"),
    path("dashboard/", public.dashboard_redirect, name="dashboard"),
    path("pending-approval/", public.pending_approval, name="pending_approval"),
    # Superadmin
    path("admin/dashboard/", admin_panel.dashboard, name="admin_dashboard"),
    path("admin/users/", admin_panel.users, name="admin_users"),
    path("admin/users/<int:pk>/", admin_panel.user_edit, name="admin_user_edit"),
    path("admin/users/<int:pk>/action/", admin_panel.user_action, name="admin_user_action"),
    path("admin/subjects/", admin_panel.subjects, name="admin_subjects"),
    path("admin/audit/", admin_panel.audit, name="admin_audit"),
    path("admin/reports/attendance.xlsx", admin_panel.school_export_xlsx, name="admin_export_xlsx"),
    path("admin/subjects/<int:pk>/class-record.xlsx", admin_panel.subject_export_xlsx, name="admin_subject_export_xlsx"),
    # Instructor
    path("instructor/dashboard/", instructor.dashboard, name="instructor_dashboard"),
    path("instructor/subjects/<int:pk>/", instructor.subject_detail, name="instructor_subject"),
    path("instructor/sessions/<int:pk>/", instructor.session_live, name="instructor_session"),
    path("instructor/sessions/<int:pk>/export/", instructor.session_export, name="instructor_session_export"),
    path("instructor/subjects/<int:pk>/class-record.xlsx", instructor.subject_export_xlsx, name="instructor_subject_export_xlsx"),
    # Student
    path("student/dashboard/", student.dashboard, name="student_dashboard"),
    path("student/scan/", student.scan, name="student_scan"),
]
