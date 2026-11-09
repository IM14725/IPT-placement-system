from .base import *  # noqa: F401,F403

DEBUG = True

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Ensure login/registration tasks execute immediately in local dev even if a
# separate Celery worker process is not running, while still storing results in
# Redis for /auth/task-status/<task_id>/ polling.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_STORE_EAGER_RESULT = True

