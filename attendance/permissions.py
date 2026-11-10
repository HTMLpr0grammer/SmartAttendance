from rest_framework.permissions import BasePermission

from .models import User


class IsActiveInstructor(BasePermission):
    message = "Only approved instructors can perform this action."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role == User.Role.INSTRUCTOR
                    and u.status == User.AccountStatus.ACTIVE)


class IsInstructorOrSuperadmin(BasePermission):
    message = "Only approved instructors or administrators can perform this action."

    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False
        if u.role == User.Role.SUPERADMIN:
            return True
        return u.role == User.Role.INSTRUCTOR and u.status == User.AccountStatus.ACTIVE


class IsStudent(BasePermission):
    message = "Only student accounts can perform this action."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role == User.Role.STUDENT)
