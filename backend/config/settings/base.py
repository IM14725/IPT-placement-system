from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, True),
    SECRET_KEY=(str, "django-insecure-dev-only-change-me"),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "testserver"]),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    # local apps
    "apps.core",
    "apps.accounts",
    "apps.students",
    "apps.companies",
    "apps.documents",
    "apps.locations",
    "apps.slots",
    "apps.applications",
    "apps.payments",
    "apps.notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Activates the per-user/site language for every request (EN / Kiswahili).
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# Internationalisation: English + Kiswahili (user-selectable).
LANGUAGE_CODE = "en"
LANGUAGES = [
    ("en", "English"),
    ("sw", "Kiswahili"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]

ROOT_URLCONF = "config.urls"

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
                "apps.core.context_processors.nav_user",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="ipt_marketplace"),
        "USER": env("DB_USER", default="ipt"),
        "PASSWORD": env("DB_PASSWORD", default="ipt_dev_password"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 0 if env("DB_POOL", default=False) else 60,
        "OPTIONS": {"pool": True} if env("DB_POOL", default=False) else {},
    }
}

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Dar_es_Salaam"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Upload limits (bytes) - strict 2MB per spec
MAX_UPLOAD_SIZE = 2 * 1024 * 1024
ALLOWED_DOC_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"}

# Locations reference data (root-level data/)
LOCATIONS_DATA_FILE = BASE_DIR.parent / "data" / "tanzania_locations.json"
WARDS_DATA_FILE = BASE_DIR.parent / "data" / "tanzania_wards.json"
INSTITUTIONS_DATA_FILE = BASE_DIR.parent / "data" / "tanzania_institutions.json"

# Celery / Redis
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300

# Redis-backed hot-key cache (apps/core/cache.py) — reduces DB hits when many
# students search the same slots at once. Disabled in tests via conftest.
CACHE_ENABLED = env("CACHE_ENABLED", default=True)
CACHE_KEY_PREFIX = env("CACHE_KEY_PREFIX", default="ipt")
CACHE_TTL_DEFAULT = env("CACHE_TTL_DEFAULT", default=60)
SLOT_SEARCH_CACHE_TTL = env("SLOT_SEARCH_CACHE_TTL", default=20)
REGIONS_CACHE_TTL = env("REGIONS_CACHE_TTL", default=3600)
VERIFICATION_CACHE_TTL = env("VERIFICATION_CACHE_TTL", default=300)

# Token-bucket rate limiting (apps/core/rate_limit.py). Disabled in tests.
RATE_LIMIT_ENABLED = env("RATE_LIMIT_ENABLED", default=True)

# Beat schedule: expire pending applications past their 3-hour payment deadline.
CELERY_BEAT_SCHEDULE = {
    "expire-unpaid-applications": {
        "task": "applications.expire_unpaid",
        "schedule": 600.0,  # every 10 minutes
    },
}

# FastAPI service (realtime)
FASTAPI_URL = env("FASTAPI_URL", default="http://127.0.0.1:8001")

# Gateway webhook shared secret (dev)
GATEWAY_WEBHOOK_SECRET = env("GATEWAY_WEBHOOK_SECRET", default="dev-webhook-secret")
