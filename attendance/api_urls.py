from django.urls import path

from . import api

urlpatterns = [
    # Instructor – sessions
    path("sessions/start/", api.StartSessionView.as_view(), name="api_session_start"),
    path("sessions/<int:pk>/qr-token/", api.QRTokenView.as_view(), name="api_qr_token"),
    path("sessions/<int:pk>/live/", api.SessionLiveView.as_view(), name="api_session_live"),
    path("sessions/<int:pk>/close/", api.CloseSessionView.as_view(), name="api_session_close"),
    path("sessions/<int:pk>/attendance/", api.AttendanceUpdateView.as_view(), name="api_attendance_update"),
    path("sessions/<int:pk>/rogue/<int:audit_id>/approve/", api.ApproveRogueScanView.as_view(),
         name="api_rogue_approve"),
    # Instructor – roster
    path("subjects/<int:pk>/enroll/", api.EnrollStudentView.as_view(), name="api_enroll"),
    path("subjects/<int:pk>/students/<int:student_id>/drop/", api.DropStudentView.as_view(), name="api_drop"),
    path("subjects/<int:pk>/students/<int:student_id>/profile/", api.StudentSubjectProfileView.as_view(),
         name="api_student_profile"),
    path("students/<int:student_id>/unbind-device/", api.UnbindDeviceView.as_view(), name="api_unbind_device"),
    # Student
    path("attendance/scan/", api.ScanAttendanceView.as_view(), name="api_scan"),
]
