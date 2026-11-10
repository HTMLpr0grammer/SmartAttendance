from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import User


def role_required(*roles, allow_pending=False):
    """
    Restrict a view to the given roles.

    Pending instructors are redirected to the "awaiting approval" page unless
    ``allow_pending`` is set. Pending students are allowed (they see a banner).
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user.role not in roles:
                raise PermissionDenied("You do not have access to this page.")
            if user.is_instructor and user.is_pending and not allow_pending:
                return redirect("pending_approval")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


superadmin_required = role_required(User.Role.SUPERADMIN)
instructor_required = role_required(User.Role.INSTRUCTOR)
student_required = role_required(User.Role.STUDENT)
