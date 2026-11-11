from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    SECRET_KEY=(str, "django-insecure-dev-only-change-me"),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "testserver"]),
)

environ.Env.read_env(BASE_DIR / ".env")

KNOWN_INSECURE_SECRET_KEYS = frozenset({
    "",
    "django-insecure-dev-only-change-me",
    "change-me",
    "secret",
})
KNOWN_INSECURE_WEBHOOK_SECRETS = frozenset({
    "",
    "dev-webhook-secret",
    "dev-mock-webhook-secret",
    "MOCK_API_SECRET",
})


def validate_production_config(
    *,
    secret_key,
    allowed_hosts,
    gateway_webhook_secret,
    selcom_api_key,
    selcom_api_secret,
    selcom_vendor_till,
    database_url,
    db_password,
):
    """Fail fast at startup if any production secret or setting is missing or uses a dev default."""
    if not secret_key or secret_key in KNOWN_INSECURE_SECRET_KEYS or secret_key.startswith("django-insecure-") or len(secret_key) < 32:
        raise ImproperlyConfigured(
            "Production requires a strong SECRET_KEY (>= 32 chars, not a dev/insecure default)."
        )
    if not allowed_hosts or set(allowed_hosts).issubset({"localhost", "127.0.0.1", "testserver"}):
        raise ImproperlyConfigured(
            "Production requires explicit ALLOWED_HOSTS (cannot be empty or localhost-only)."
        )
    if not gateway_webhook_secret or gateway_webhook_secret in KNOWN_INSECURE_WEBHOOK_SECRETS or len(gateway_webhook_secret) < 16:
        raise ImproperlyConfigured(
            "Production requires a strong GATEWAY_WEBHOOK_SECRET (cannot use dev default)."
        )
    if not selcom_api_key or selcom_api_key == "MOCK_API_KEY":
        raise ImproperlyConfigured(
            "Production requires a real SELCOM_API_KEY (cannot use MOCK_API_KEY)."
        )
    if not selcom_api_secret or selcom_api_secret in KNOWN_INSECURE_WEBHOOK_SECRETS:
        raise ImproperlyConfigured(
            "Production requires a real SELCOM_API_SECRET (cannot use MOCK_API_SECRET)."
        )
    if not selcom_vendor_till or selcom_vendor_till == "MOCK_TILL":
        raise ImproperlyConfigured(
            "Production requires a real SELCOM_VENDOR_TILL (cannot use MOCK_TILL)."
        )
    if not database_url and (not db_password or db_password == "ipt_dev_password"):
        raise ImproperlyConfigured(
            "Production requires DATABASE_URL or a non-default DB_PASSWORD."
        )
    if database_url and "ipt_dev_password" in database_url:
        raise ImproperlyConfigured(
            "Production DATABASE_URL cannot use the default development password."
        )


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
    "apps.core.security.SecurityHeadersMiddleware",
    "apps.core.ajax.AjaxRedirectMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Activates the per-user/site language for every request (EN / Kiswahili).
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.sessions.SessionPolicyMiddleware",
    "apps.accounts.gates.AccountGateMiddleware",
    "apps.core.security.MaintenanceModeMiddleware",
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
                "apps.core.context_processors.tc_update_banner",
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
        "HOST": env("DB_HOST", default="127.0.0.1"),
        "PORT": env("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 0 if env("DB_POOL", default=False) else 60,
        "OPTIONS": {"pool": True} if env("DB_POOL", default=False) else {},
    }
}

AUTH_USER_MODEL = "accounts.User"

# Sessions: a hard 10-minute lifetime enforced on the server (apps/accounts/sessions.py),
# one live session per account, HttpOnly + SameSite cookies. Nothing is kept in JS storage.
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=600)  # idle window, restarted by activity
SESSION_ABSOLUTE_MAX_SECONDS = env.int("SESSION_ABSOLUTE_MAX_SECONDS", default=3 * 60 * 60)  # hard ceiling from login
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"  # Lax: the payment gateway returns users via top-level GET
SESSION_SAVE_EVERY_REQUEST = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)

# Administrator MFA. TOTP or a security key for admins; a FIDO2/WebAuthn key is mandatory for
# Super Admins and is the only factor accepted for high-risk actions (hardware re-verification).
ADMIN_MFA_ENFORCED = env.bool("ADMIN_MFA_ENFORCED", default=True)
SUPER_ADMIN_REQUIRE_FIDO2 = env.bool("SUPER_ADMIN_REQUIRE_FIDO2", default=True)
HARDWARE_REVERIFY_SECONDS = env.int("HARDWARE_REVERIFY_SECONDS", default=300)
ADMIN_TEMP_PASSWORD_HOURS = env.int("ADMIN_TEMP_PASSWORD_HOURS", default=24)
ADMIN_RECOVERY_APPROVALS = env.int("ADMIN_RECOVERY_APPROVALS", default=2)
ADMIN_RECOVERY_HOURS = env.int("ADMIN_RECOVERY_HOURS", default=24)
# WebAuthn relying party. Left blank = derived from the request host (works on localhost).
WEBAUTHN_RP_ID = env("WEBAUTHN_RP_ID", default="")
WEBAUTHN_RP_NAME = env("WEBAUTHN_RP_NAME", default="IPT Marketplace")

# Temporary login lockout (apps/core/lockout.py): never a permanent account lock.
LOGIN_LOCKOUT_ATTEMPTS = env.int("LOGIN_LOCKOUT_ATTEMPTS", default=5)
LOGIN_LOCKOUT_SECONDS = env.int("LOGIN_LOCKOUT_SECONDS", default=300)

LOGIN_URL = "/auth/login/"
LOGIN_REDIRECT_URL = "/"

# Argon2id first: ~0.1s to verify. PBKDF2 at Django's default 1.5M iterations took 3+ seconds per login on
# the dev machine. Older PBKDF2 hashes still verify and are re-hashed to Argon2 on the owner's next login.
PASSWORD_HASHERS = [
    "apps.accounts.hashers.IPTArgon2PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "apps.accounts.validators.BreachedPasswordValidator"},
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
        # Session only: permanent API tokens would bypass the session lifetime rules.
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

if env("SMTP_HOST", default=""):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = env("SMTP_HOST")
    EMAIL_PORT = env.int("SMTP_PORT", default=587)
    EMAIL_HOST_USER = env("SMTP_USER", default="")
    EMAIL_HOST_PASSWORD = env("SMTP_PASSWORD", default="")
    EMAIL_USE_TLS = env.bool("SMTP_USE_TLS", default=True)
    DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    DEFAULT_FROM_EMAIL = "noreply@iptmarketplace.com"

# SMS Configuration
BEEM_API_KEY = env("BEEM_API_KEY", default="")
BEEM_SECRET_KEY = env("BEEM_SECRET_KEY", default="")
BEEM_SENDER_ID = env("BEEM_SENDER_ID", default="IPT")

# Payment Configuration (Selcom)
SELCOM_BASE_URL = env("SELCOM_BASE_URL", default="https://apitest.selcommobile.com")
SELCOM_API_KEY = env("SELCOM_API_KEY", default="MOCK_API_KEY")
SELCOM_API_SECRET = env("SELCOM_API_SECRET", default="MOCK_API_SECRET")
SELCOM_VENDOR_TILL = env("SELCOM_VENDOR_TILL", default="MOCK_TILL")

# Upload limits (bytes) - strict 2MB per spec
MAX_UPLOAD_SIZE = 2 * 1024 * 1024
ALLOWED_DOC_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"}

# Locations reference data (root-level data/)
LOCATIONS_DATA_FILE = BASE_DIR.parent / "data" / "tanzania_locations.json"
WARDS_DATA_FILE = BASE_DIR.parent / "data" / "tanzania_wards.json"
INSTITUTIONS_DATA_FILE = BASE_DIR.parent / "data" / "tanzania_institutions.json"

# Celery / Redis
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://127.0.0.1:6379/2")
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300

# Public welcome page: a trust counter only appears once it reaches its minimum, so an
# early-stage platform never advertises tiny numbers.
PUBLIC_STATS_MIN = {
    "organizations": 10,
    "slots": 20,
    "universities": 5,
    "students": 100,
    "regions": 3,
}

# Redis-backed hot-key cache (apps/core/cache.py) — reduces DB hits when many
# students search the same slots at once. Disabled in tests via conftest.
CACHE_ENABLED = env("CACHE_ENABLED", default=True)
CACHE_KEY_PREFIX = env("CACHE_KEY_PREFIX", default="ipt")
CACHE_TTL_DEFAULT = env("CACHE_TTL_DEFAULT", default=60)
SLOT_SEARCH_CACHE_TTL = env("SLOT_SEARCH_CACHE_TTL", default=20)
REGIONS_CACHE_TTL = env("REGIONS_CACHE_TTL", default=3600)
VERIFICATION_CACHE_TTL = env("VERIFICATION_CACHE_TTL", default=300)

# Token-bucket rate limiting (apps/core/rate_limit.py). Disabled in tests.
# Keyless bot check (honeypot + signed form timestamp) on login and register; see apps/accounts/antibot.py.
ANTIBOT_ENABLED = env.bool("ANTIBOT_ENABLED", default=True)
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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "security": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "security",
        },
    },
    "loggers": {
        "security": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
