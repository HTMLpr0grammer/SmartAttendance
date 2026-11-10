from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect


class AccountStatusMiddleware:
    """Immediately sign out users who were suspended or archived mid-session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_suspended:
            logout(request)
            msg = "Your account has been suspended. Please contact the administrator."
            if request.path.startswith("/api/"):
                return JsonResponse({"ok": False, "detail": msg}, status=403)
            messages.error(request, msg)
            return redirect("login")
        return self.get_response(request)
