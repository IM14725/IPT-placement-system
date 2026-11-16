from .base import *  # noqa: F401,F403

DEBUG = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Local dev uses the console SMS mock unless SMS_BACKEND=astracall is set in backend/.env (a key in
# .env does not switch dev to live SMS on its own). Mock-sent codes are shown on the verify page
# and on the admin "OTP requests" page.
SMS_BACKEND = env("SMS_BACKEND", default="mock")
OTP_STORE_MOCK_CODE = True

# Ensure login/registration tasks execute immediately in local dev even if a
# separate Celery worker process is not running, while still storing results in
# Redis for /auth/task-status/<task_id>/ polling.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_STORE_EAGER_RESULT = True

# Admin MFA (hardware key) is skipped in local dev, where WebAuthn needs a real domain/key.
# Set ADMIN_MFA_ENFORCED=True in backend/.env to exercise the full flow locally.
ADMIN_MFA_ENFORCED = env.bool("ADMIN_MFA_ENFORCED", default=False)


# Serve static files through WhiteNoise in dev too (same code path as production, no collectstatic needed).
# Static files are sent as-is with a Content-Length. Do NOT compress them on the fly here: GZipMiddleware turns a
# static file into a chunked stream, and the dev server (HTTP/1.0 style) behind a tunnel or proxy (dev tunnels,
# ngrok) then never finishes the response, so CSS/JS hang. HTML is still gzipped (small, has a Content-Length).
INSTALLED_APPS = ["whitenoise.runserver_nostatic", *INSTALLED_APPS]
WHITENOISE_USE_FINDERS = True

import warnings
warnings.filterwarnings("ignore", message="No directory at")
