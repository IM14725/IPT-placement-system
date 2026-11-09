from .base import *  # noqa: F401,F403
import os
import dj_database_url

DEBUG = False

SECRET_KEY = os.environ.get("SECRET_KEY", "")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
GATEWAY_WEBHOOK_SECRET = os.environ.get("GATEWAY_WEBHOOK_SECRET", "")
SELCOM_API_KEY = os.environ.get("SELCOM_API_KEY", "")
SELCOM_API_SECRET = os.environ.get("SELCOM_API_SECRET", "")
SELCOM_VENDOR_TILL = os.environ.get("SELCOM_VENDOR_TILL", "")
_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

validate_production_config(
    secret_key=SECRET_KEY,
    allowed_hosts=ALLOWED_HOSTS,
    gateway_webhook_secret=GATEWAY_WEBHOOK_SECRET,
    selcom_api_key=SELCOM_API_KEY,
    selcom_api_secret=SELCOM_API_SECRET,
    selcom_vendor_till=SELCOM_VENDOR_TILL,
    database_url=_DATABASE_URL,
    db_password=_DB_PASSWORD,
)

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True

if _DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(default=_DATABASE_URL, conn_max_age=60)
    }

