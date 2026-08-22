from .base import *  # noqa: F401,F403

DEBUG = False

SECRET_KEY = env("SECRET_KEY")

# In production override these:
# EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# EMAIL_HOST = env("EMAIL_HOST")
# EMAIL_HOST_USER = env("EMAIL_HOST_USER")
# EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
# EMAIL_PORT = env("EMAIL_PORT", default=587)
# EMAIL_USE_TLS = True
# DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True