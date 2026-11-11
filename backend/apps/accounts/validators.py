"""Password validators beyond Django's built-ins.

``BreachedPasswordValidator`` rejects passwords found in a bundled list of the ~46,000 most
commonly leaked passwords of 8+ characters (UK NCSC "most used passwords", public data). It is fully offline:
the password never leaves the server, and there is no API key or external call.
"""

from functools import lru_cache
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

_LIST_PATH = Path(__file__).resolve().parent / "data" / "breached_passwords.txt"


@lru_cache(maxsize=1)
def _breached():
    try:
        return frozenset(line.strip() for line in _LIST_PATH.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:  # a missing list must never block signups
        return frozenset()


class BreachedPasswordValidator:
    def validate(self, password, user=None):
        if password.strip().lower() in _breached():
            raise ValidationError(
                _("This password has appeared in known data leaks. Please choose a different one."),
                code="password_breached",
            )

    def get_help_text(self):
        return _("Your password can't be one that is known from data leaks.")
