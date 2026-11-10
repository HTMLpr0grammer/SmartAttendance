"""
Django admin registration with SAFE DATA MANAGEMENT:
hard deletes of users, subjects and sessions are disabled so cascades can
never wipe institutional attendance/audit history. Use archive (soft delete).
"""
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

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


class NoDeleteMixin:
    def has_delete_permission(self, request, obj=None):
        return False


class AddressInline(admin.StackedInline):
    model = Address
    can_delete = False


class StudentProfileInline(admin.StackedInline):
    model = StudentProfile
    can_delete = False


@admin.register(User)
class UserAdmin(NoDeleteMixin, BaseUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "status", "is_active", "has_device")
    list_filter = ("role", "status", "is_active")
    search_fields = ("username", "email", "first_name", "last_name", "student_profile__student_id_number")
    readonly_fields = ("date_joined", "last_login")
    fieldsets = BaseUserAdmin.fieldsets[:1] + (
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        ("SmartAttendance", {"fields": ("role", "status", "device_id")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("username", "email", "role", "status", "password1", "password2")}),
    )
    inlines = [AddressInline, StudentProfileInline]
    actions = ["approve", "suspend", "archive", "restore", "unbind_device"]

    @admin.display(boolean=True, description="Device bound")
    def has_device(self, obj):
        return bool(obj.device_id)

    @admin.action(description="Approve / activate selected accounts")
    def approve(self, request, qs):
        n = qs.update(status=User.AccountStatus.ACTIVE)
        self.message_user(request, f"{n} account(s) activated.", messages.SUCCESS)

    @admin.action(description="Suspend selected accounts")
    def suspend(self, request, qs):
        n = qs.exclude(pk=request.user.pk).update(status=User.AccountStatus.SUSPENDED)
        self.message_user(request, f"{n} account(s) suspended.", messages.WARNING)

    @admin.action(description="Archive (soft delete) selected accounts")
    def archive(self, request, qs):
        n = qs.exclude(pk=request.user.pk).update(is_active=False)
        self.message_user(request, f"{n} account(s) archived.", messages.WARNING)

    @admin.action(description="Restore archived accounts")
    def restore(self, request, qs):
        n = qs.update(is_active=True)
        self.message_user(request, f"{n} account(s) restored.", messages.SUCCESS)

    @admin.action(description="Unbind device")
    def unbind_device(self, request, qs):
        n = qs.update(device_id=None)
        self.message_user(request, f"{n} device binding(s) cleared.", messages.SUCCESS)


@admin.register(Department)
class DepartmentAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)


@admin.register(Subject)
class SubjectAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("subject_code", "title", "teacher", "department")
    list_filter = ("department",)
    search_fields = ("subject_code", "title")
    autocomplete_fields = ("teacher",)


@admin.register(SubjectEnrollment)
class SubjectEnrollmentAdmin(admin.ModelAdmin):
    """Deleting an enrollment = dropping a student. The User row is never touched."""

    list_display = ("student", "subject", "enrolled_at")
    list_filter = ("subject",)
    search_fields = ("student__username", "student__last_name", "student__student_profile__student_id_number")
    autocomplete_fields = ("student",)


@admin.register(ClassSession)
class ClassSessionAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("subject", "session_title", "date", "created_at", "expires_at", "allowed_radius_meters", "is_active")
    list_filter = ("is_active", "subject")
    date_hierarchy = "date"


@admin.register(AttendanceLog)
class AttendanceLogAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("student", "session", "status", "scanned_at")
    list_filter = ("status", "session__subject")
    search_fields = ("student__username", "student__last_name")


@admin.register(AuditScanLog)
class AuditScanLogAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("created_at", "reason", "student", "session", "submitted_device_id")
    list_filter = ("reason",)
    readonly_fields = [f.name for f in AuditScanLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
