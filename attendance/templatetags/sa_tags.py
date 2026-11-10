from django import template

register = template.Library()

STATUS_BADGES = {
    "present": "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
    "late": "bg-amber-100 text-amber-800 ring-amber-600/20",
    "absent": "bg-rose-100 text-rose-800 ring-rose-600/20",
    "active": "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
    "pending": "bg-amber-100 text-amber-800 ring-amber-600/20",
    "suspended": "bg-rose-100 text-rose-800 ring-rose-600/20",
    "archived": "bg-slate-200 text-slate-700 ring-slate-500/20",
    "Good Standing": "bg-emerald-100 text-emerald-800 ring-emerald-600/20",
    "Frequent Tardy": "bg-amber-100 text-amber-800 ring-amber-600/20",
    "Critical Absences": "bg-rose-100 text-rose-800 ring-rose-600/20",
    "unenrolled_student": "bg-sky-100 text-sky-800 ring-sky-600/20",
    "device_mismatch": "bg-rose-100 text-rose-800 ring-rose-600/20",
    "out_of_bounds": "bg-orange-100 text-orange-800 ring-orange-600/20",
    "expired_token": "bg-slate-200 text-slate-700 ring-slate-500/20",
    "session_closed": "bg-violet-100 text-violet-800 ring-violet-600/20",
    "admin": "bg-indigo-100 text-indigo-800 ring-indigo-600/20",
    "instructor": "bg-sky-100 text-sky-800 ring-sky-600/20",
    "student": "bg-teal-100 text-teal-800 ring-teal-600/20",
}


@register.filter
def badge(value):
    """Tailwind classes for a status/standing/reason pill."""
    return STATUS_BADGES.get(str(value), "bg-slate-100 text-slate-700 ring-slate-500/20")


@register.filter
def user_status_key(user):
    return "archived" if not user.is_active else user.status


@register.simple_tag(takes_context=True)
def qs_replace(context, **kwargs):
    """Rebuild the current querystring with some keys replaced (used for pagination)."""
    query = context["request"].GET.copy()
    for k, v in kwargs.items():
        query[k] = v
    return query.urlencode()
