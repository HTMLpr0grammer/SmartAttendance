"""
Django settings for SmartAttendance.

Most values can be overridden with environment variables so the same file
works for local development (SQLite) and production (PostgreSQL).
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --------------------------------------------------------------------------
# .env file (optional). Lets PythonAnywhere – or any host – keep secrets in a file next to
# manage.py instead of in code. Real environment variables always win over the file.
# --------------------------------------------------------------------------
def load_env_file(path):
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
DEV_SECRET_KEY = "dev-insecure-smartattendance-key-change-me-in-production-0123456789abcdef"
DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "") or DEV_SECRET_KEY
if not DEBUG and (SECRET_KEY == DEV_SECRET_KEY or len(SECRET_KEY) < 40):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set to a long random value when DJANGO_DEBUG is off. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(50))\""
    )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0,*" if DEBUG else "")
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must list your domain when DJANGO_DEBUG is off, "
        "e.g. DJANGO_ALLOWED_HOSTS=yourname.pythonanywhere.com"
    )

# Origins allowed to submit forms / API calls. In production every allowed host is trusted over HTTPS
# automatically; add extra origins (e.g. an ngrok tunnel) with DJANGO_CSRF_TRUSTED_ORIGINS.
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
if not DEBUG:
    CSRF_TRUSTED_ORIGINS += [
        f"https://{h}" for h in ALLOWED_HOSTS
        if h not in {"*", "localhost", "127.0.0.1", "0.0.0.0"} and f"https://{h}" not in CSRF_TRUSTED_ORIGINS
    ]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "attendance.apps.AttendanceConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "attendance.middleware.AccountStatusMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "smartattendance.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "attendance.context_processors.app_context",
            ],
        },
    },
]

WSGI_APPLICATION = "smartattendance.wsgi.application"
ASGI_APPLICATION = "smartattendance.asgi.application"

# --------------------------------------------------------------------------
# Database – SQLite by default, PostgreSQL when DB_ENGINE=postgres
# --------------------------------------------------------------------------
if os.environ.get("DB_ENGINE", "sqlite").lower() in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "smartattendance"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
AUTH_USER_MODEL = "attendance.User"
AUTHENTICATION_BACKENDS = ["attendance.backends.EmailOrUsernameBackend"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# I18N / TZ
# --------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Django REST Framework
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"]
    + (["rest_framework.renderers.BrowsableAPIRenderer"] if DEBUG else []),
}

# --------------------------------------------------------------------------
# SmartAttendance business rules
# --------------------------------------------------------------------------
SMARTATTENDANCE = {
    "APP_NAME": "SmartAttendance",
    "SCHOOL_NAME": "Zamboanga del Sur Provincial Government College",
    "SCHOOL_SHORT": "ZDSPGC",
    # JWT used inside the rotating QR code
    "QR_JWT_SECRET": os.environ.get("QR_JWT_SECRET", SECRET_KEY),
    "QR_JWT_ALGORITHM": "HS256",
    "QR_TOKEN_TTL_SECONDS": 10,
    # Small tolerance for network latency between scan and POST
    "QR_TOKEN_LEEWAY_SECONDS": int(os.environ.get("QR_TOKEN_LEEWAY_SECONDS", 2)),
    # Instructor screen refresh interval (must be < TTL)
    "QR_REFRESH_SECONDS": 8,
    # How often the instructor's live screen re-fetches the roster / rogue-scan feed.
    # (The QR code itself still rotates every QR_REFRESH_SECONDS.) Polling stops once the session closes.
    "LIVE_POLL_SECONDS": int(os.environ.get("LIVE_POLL_SECONDS", 30)),
    # Default "late after" minutes; instructors can choose 10, 15, 30 or a custom value per session.
    "LATE_THRESHOLD_MINUTES": 15,
    "DEFAULT_RADIUS_METERS": 40,
    # Punctuality standing thresholds
    "CRITICAL_ABSENCES": 3,
    "CRITICAL_ATTENDANCE_RATE": 80.0,
    "FREQUENT_TARDY_COUNT": 3,
}

# --------------------------------------------------------------------------
# Security hardening for production (DJANGO_DEBUG=False)
# --------------------------------------------------------------------------
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    # PythonAnywhere (and most hosts) terminate HTTPS in front of Django and say so in this header.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Off by default: on PythonAnywhere use the Web tab's "Force HTTPS" switch instead,
    # which redirects before the request reaches Django. Set to True on other hosts if needed.
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
    # W005/W021: HSTS subdomains/preload are for domains you own outright, not *.pythonanywhere.com.
    SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
    if not SECURE_SSL_REDIRECT:
        SILENCED_SYSTEM_CHECKS.append("security.W008")
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", 60 * 60 * 24 * 30))
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

# --------------------------------------------------------------------------
# Logging – errors go to stderr, which PythonAnywhere shows in the web app's error log.
# --------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "WARNING"), "propagate": False},
        # Internet bots constantly probe public sites with fake Host headers; don't fill the error log with them.
        "django.security.DisallowedHost": {"handlers": [], "level": "CRITICAL", "propagate": False},
    },
}
