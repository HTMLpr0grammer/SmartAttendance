from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "SmartAttendance – Django Admin"
admin.site.site_title = "SmartAttendance"

urlpatterns = [
    # Django's built-in admin is moved so that /admin/dashboard/ belongs to
    # the SmartAttendance Superadmin dashboard.
    path("django-admin/", admin.site.urls),
    path("api/", include("attendance.api_urls")),
    path("", include("attendance.urls")),
]
