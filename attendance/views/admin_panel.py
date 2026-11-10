"""Superadmin dashboard: approvals, user/subject/department management, analytics, audit feed."""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ..decorators import superadmin_required
from ..forms import AddressForm, DepartmentForm, StudentProfileForm, SubjectForm, UserAdminForm
from ..exports import XLSX_CONTENT_TYPE, build_school_workbook, build_subject_workbook
from ..models import Address, AttendanceLog, AuditScanLog, ClassSession, Department, Subject, User
from ..services import close_stale_sessions, punctuality_summary, student_rows_with_standing, watchlist


@superadmin_required
def dashboard(request):
    close_stale_sessions()
    today = timezone.localdate()
    since = timezone.now() - timedelta(days=7)

    users = User.objects.filter(is_active=True)
    kpis = {
        "students": users.filter(role=User.Role.STUDENT).count(),
        "instructors": users.filter(role=User.Role.INSTRUCTOR, status=User.AccountStatus.ACTIVE).count(),
        "pending": users.filter(status=User.AccountStatus.PENDING).exclude(role=User.Role.SUPERADMIN).count(),
        "sessions_today": ClassSession.objects.filter(date=today).count(),
        "live_sessions": ClassSession.objects.filter(is_active=True, expires_at__gte=timezone.now()).count(),
        "alerts_week": AuditScanLog.objects.filter(created_at__gte=since).count(),
    }
    overall = punctuality_summary(AttendanceLog.objects.all())

    students = User.objects.filter(role=User.Role.STUDENT, is_active=True).select_related("student_profile")
    rows = student_rows_with_standing(students)
    flagged = watchlist(rows)  # merge sort: critical cases first, then lowest attendance rate

    reason_breakdown = (
        AuditScanLog.objects.values("reason").annotate(n=Count("id")).order_by("-n")
    )
    reason_labels = dict(AuditScanLog.Reason.choices)

    ctx = {
        "kpis": kpis,
        "export_departments": Department.objects.filter(is_active=True),
        "overall": overall,
        "pending_instructors": User.objects.filter(
            role=User.Role.INSTRUCTOR, status=User.AccountStatus.PENDING, is_active=True
        ).select_related("address").order_by("date_joined"),
        "pending_students": User.objects.filter(
            role=User.Role.STUDENT, status=User.AccountStatus.PENDING, is_active=True
        ).select_related("student_profile").order_by("date_joined")[:10],
        "watchlist": flagged[:12],
        "audit_feed": AuditScanLog.objects.select_related("student", "session__subject")[:12],
        "reason_breakdown": [
            {"reason": r["reason"], "label": reason_labels.get(r["reason"], r["reason"]), "n": r["n"]}
            for r in reason_breakdown
        ],
    }
    return render(request, "admin_panel/dashboard.html", ctx)


@superadmin_required
def users(request):
    qs = User.objects.select_related("student_profile", "address").order_by("role", "last_name", "first_name")
    role = request.GET.get("role", "")
    status = request.GET.get("status", "")
    q = request.GET.get("q", "").strip()
    if role:
        qs = qs.filter(role=role)
    if status == "archived":
        qs = qs.filter(is_active=False)
    elif status:
        qs = qs.filter(status=status, is_active=True)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(username__icontains=q)
            | Q(email__icontains=q) | Q(student_profile__student_id_number__icontains=q)
            | Q(device_id=q)  # paste a device ID from the audit log to see which account holds it
        )
    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "admin_panel/users.html", {
        "page": page, "role": role, "status": status, "q": q,
        "roles": User.Role.choices, "statuses": User.AccountStatus.choices,
    })


@superadmin_required
def user_edit(request, pk):
    target = get_object_or_404(User.objects.select_related("student_profile", "address"), pk=pk)
    address = getattr(target, "address", None) or Address(user=target)
    profile = getattr(target, "student_profile", None)

    data = request.POST if request.method == "POST" else None
    uform = UserAdminForm(data, instance=target, prefix="u")
    aform = AddressForm(data, instance=address, prefix="a")
    pform = StudentProfileForm(data, instance=profile, prefix="p") if (target.is_student or profile) else None

    if request.method == "POST":
        # Accounts created outside the sign-up form (e.g. createsuperuser) have no address yet;
        # only require one if it already exists or the admin typed something into it.
        save_address = address.pk is not None or aform.has_changed()
        valid = (uform.is_valid() and (not save_address or aform.is_valid())
                 and (pform is None or pform.is_valid()))
        if valid:
            own = target.pk == request.user.pk
            if own and uform.cleaned_data["role"] != User.Role.SUPERADMIN:
                messages.error(request, "You cannot remove your own Superadmin role.")
            elif own and uform.cleaned_data["status"] != User.AccountStatus.ACTIVE:
                messages.error(request, "You cannot suspend or deactivate your own account.")
            else:
                uform.save()
                if save_address:
                    addr = aform.save(commit=False)
                    addr.user = target
                    addr.save()
                if pform is not None:
                    prof = pform.save(commit=False)
                    prof.user = target
                    prof.save()
                messages.success(request, f"Profile for {target} updated.")
                return redirect("admin_users")
    stats = punctuality_summary(AttendanceLog.objects.filter(student=target)) if target.is_student else None
    return render(request, "admin_panel/user_edit.html", {
        "target": target, "uform": uform, "aform": aform, "pform": pform, "stats": stats,
        "subjects_taught": target.assigned_subjects.all() if target.is_instructor else None,
        "enrollments": target.enrolled_subjects.select_related("subject") if target.is_student else None,
    })


@superadmin_required
@require_POST
def user_action(request, pk):
    target = get_object_or_404(User, pk=pk)
    action = request.POST.get("action")
    nxt = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        nxt = "admin_users"

    if target.pk == request.user.pk and action in {"suspend", "archive"}:
        messages.error(request, "You cannot suspend or archive your own account.")
        return redirect(nxt)

    S = User.AccountStatus
    if action == "approve":
        target.status = S.ACTIVE
        target.save(update_fields=["status"])
        messages.success(request, f"{target} approved and activated.")
    elif action == "suspend":
        target.status = S.SUSPENDED
        target.save(update_fields=["status"])
        messages.warning(request, f"{target} suspended.")
    elif action == "reactivate":
        target.status = S.ACTIVE
        target.save(update_fields=["status"])
        messages.success(request, f"{target} reactivated.")
    elif action == "archive":
        # Soft delete – the row and its attendance/audit history are preserved.
        target.is_active = False
        target.save(update_fields=["is_active"])
        messages.warning(request, f"{target} archived. Historical records are preserved.")
    elif action == "restore":
        target.is_active = True
        target.save(update_fields=["is_active"])
        messages.success(request, f"{target} restored.")
    elif action == "unbind":
        target.device_id = None
        target.save(update_fields=["device_id"])
        messages.success(request, f"Device binding cleared for {target}.")
    else:
        messages.error(request, "Unknown action.")
    return redirect(nxt)


@superadmin_required
def subjects(request):
    subject_form = SubjectForm(prefix="s")
    dept_form = DepartmentForm(prefix="d")

    if request.method == "POST":
        kind = request.POST.get("form")
        if kind == "subject":
            subject_form = SubjectForm(request.POST, prefix="s")
            if subject_form.is_valid():
                s = subject_form.save()
                messages.success(request, f"Subject {s.subject_code} created.")
                return redirect("admin_subjects")
        elif kind == "department":
            dept_form = DepartmentForm(request.POST, prefix="d")
            if dept_form.is_valid():
                d = dept_form.save()
                messages.success(request, f"Department {d.code} created.")
                return redirect("admin_subjects")
        elif kind == "reassign":
            subj = get_object_or_404(Subject, pk=request.POST.get("subject_id"))
            teacher = User.objects.filter(
                pk=request.POST.get("teacher_id"), role=User.Role.INSTRUCTOR,
                status=User.AccountStatus.ACTIVE, is_active=True,
            ).first()
            dept_id = request.POST.get("department_id") or None
            if teacher is None:
                messages.error(request, "Choose an active instructor.")
            else:
                subj.teacher = teacher
                subj.department = Department.objects.filter(pk=dept_id).first() if dept_id else None
                subj.save(update_fields=["teacher", "department"])
                messages.success(request, f"{subj.subject_code} updated.")
            return redirect("admin_subjects")
        elif kind == "toggle_department":
            d = get_object_or_404(Department, pk=request.POST.get("department_id"))
            d.is_active = not d.is_active
            d.save(update_fields=["is_active"])
            messages.info(request, f"Department {d.code} {'activated' if d.is_active else 'deactivated'}.")
            return redirect("admin_subjects")

    subject_list = Subject.objects.select_related("teacher", "department").annotate(
        n_students=Count("enrollments", distinct=True), n_sessions=Count("sessions", distinct=True)
    )
    departments = Department.objects.annotate(n_subjects=Count("subjects"))
    instructors = User.objects.filter(role=User.Role.INSTRUCTOR, status=User.AccountStatus.ACTIVE, is_active=True)
    return render(request, "admin_panel/subjects.html", {
        "subjects": subject_list, "departments": departments, "instructors": instructors,
        "subject_form": subject_form, "dept_form": dept_form,
    })


@superadmin_required
def audit(request):
    qs = AuditScanLog.objects.select_related("student", "student__student_profile", "session__subject")
    reason = request.GET.get("reason", "")
    q = request.GET.get("q", "").strip()
    if reason:
        qs = qs.filter(reason=reason)
    if q:
        qs = qs.filter(
            Q(student__first_name__icontains=q) | Q(student__last_name__icontains=q)
            | Q(student__student_profile__student_id_number__icontains=q)
            | Q(session__subject__subject_code__icontains=q)
        )
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "admin_panel/audit.html", {
        "page": page, "reason": reason, "q": q, "reasons": AuditScanLog.Reason.choices,
    })


def _xlsx(data, filename):
    resp = HttpResponse(data, content_type=XLSX_CONTENT_TYPE)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


def _parse_date(value):
    from datetime import date
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise ValueError(f"“{value}” is not a valid date.")


@superadmin_required
def school_export_xlsx(request):
    """Institution-wide Excel report: subjects, student standing and rejected scans."""
    try:
        date_from = _parse_date(request.GET.get("from", "").strip())
        date_to = _parse_date(request.GET.get("to", "").strip())
    except ValueError as exc:
        messages.error(request, f"{exc} Use the date picker.")
        return redirect("admin_dashboard")
    if date_from and date_to and date_from > date_to:
        messages.error(request, "The start date must be on or before the end date.")
        return redirect("admin_dashboard")
    dept_id = request.GET.get("department", "")
    department = Department.objects.filter(pk=dept_id).first() if dept_id.isdigit() else None
    close_stale_sessions()
    stamp = timezone.localtime().strftime("%Y%m%d")
    suffix = f"_{department.code}" if department else ""
    return _xlsx(build_school_workbook(date_from, date_to, department), f"SmartAttendance_report{suffix}_{stamp}.xlsx")


@superadmin_required
def subject_export_xlsx(request, pk):
    """Excel class record of any subject (same file instructors download)."""
    subject = get_object_or_404(Subject.objects.select_related("teacher"), pk=pk)
    close_stale_sessions([subject.pk])
    stamp = timezone.localtime().strftime("%Y%m%d")
    return _xlsx(build_subject_workbook(subject), f"{subject.subject_code}_class_record_{stamp}.xlsx")
