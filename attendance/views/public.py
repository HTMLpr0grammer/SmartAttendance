from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ..forms import LoginForm, RegistrationForm
from ..models import AttendanceLog, ClassSession, Subject, User


def landing(request):
    stats = {
        "students": User.objects.filter(role=User.Role.STUDENT, is_active=True).count(),
        "subjects": Subject.objects.count(),
        "sessions": ClassSession.objects.count(),
        "scans": AttendanceLog.objects.exclude(scanned_device_id="").count(),
    }
    return render(request, "landing.html", {"stats": stats})


class SmartLoginView(LoginView):
    template_name = "login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="attendance.backends.EmailOrUsernameBackend")
        if user.is_instructor:
            messages.info(request, "Registration received. A Superadmin must approve your instructor account.")
        else:
            messages.success(request, "Welcome to SmartAttendance! Your account is awaiting enrollment by an instructor.")
        return redirect("dashboard")
    return render(request, "register.html", {"form": form})


@login_required
def dashboard_redirect(request):
    user = request.user
    if user.is_superadmin:
        return redirect("admin_dashboard")
    if user.is_instructor:
        return redirect("pending_approval" if user.is_pending else "instructor_dashboard")
    return redirect("student_dashboard")


@login_required
def pending_approval(request):
    if not (request.user.is_instructor and request.user.is_pending):
        return redirect("dashboard")
    return render(request, "pending_approval.html")
