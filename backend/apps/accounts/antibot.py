"""Keyless bot defence for the login and registration forms (no external service, no API key).

Two cheap checks that real people never trip:

* a honeypot field hidden from people but filled in by naive bots;
* a signed timestamp issued when the form is rendered: a submission that arrives faster than a
  person could type, or with a missing/forged/expired stamp, is rejected.

Both failures give the same generic message and never reveal which check fired. This sits on top of
the existing rate limits and lockouts; it does not replace them.
"""

import logging
import time

from django import forms
from django.conf import settings
from django.core.signing import BadSignature, TimestampSigner
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

SALT = "ipt.antibot.v1"
MAX_AGE_SECONDS = 6 * 3600
GENERIC_MESSAGE = _("We couldn't verify this request. Please reload the page and try again.")


def issue_stamp():
    return TimestampSigner(salt=SALT).sign(str(int(time.time())))


def stamp_age(stamp):
    """Seconds since the stamp was issued, or None when it is missing, forged or expired."""
    if not stamp:
        return None
    try:
        TimestampSigner(salt=SALT).unsign(stamp, max_age=MAX_AGE_SECONDS)
        return time.time() - int(stamp.rsplit(":", 2)[0])
    except (BadSignature, ValueError):
        return None


class AntiBotMixin(forms.Form):
    """Add to a form (before forms.Form / forms.ModelForm in the bases) and render both fields."""

    website = forms.CharField(
        required=False,
        label="",
        widget=forms.TextInput(
            attrs={
                "tabindex": "-1",
                "autocomplete": "off",
                "aria-hidden": "true",
                # Keep password managers (Chrome, LastPass, 1Password, Bitwarden) from filling the trap.
                "data-lpignore": "true",
                "data-1p-ignore": "true",
                "data-bwignore": "true",
                "data-form-type": "other",
            }
        ),
    )
    form_ts = forms.CharField(required=False, widget=forms.HiddenInput)
    antibot_min_seconds = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._submitted_stamp = self.data.get("form_ts") if self.is_bound else None
        # Always hand out a fresh stamp, including when a bound form is re-rendered with errors.
        fresh = issue_stamp()
        self.fields["form_ts"].initial = fresh
        if self.is_bound:
            self.data = self.data.copy()
            self.data["form_ts"] = fresh

    def clean(self):
        cleaned = super().clean()
        if not getattr(settings, "ANTIBOT_ENABLED", True):
            return cleaned
        age = stamp_age(self._submitted_stamp)
        trap = (self.cleaned_data.get("website") or "").strip()
        if trap and trap.lower() == str(self.data.get("email", "")).strip().lower():
            trap = ""  # a password manager filled the saved username into the trap: not a bot
        if trap or age is None or age < self.antibot_min_seconds:
            # The visitor only ever sees the generic message; the reason is logged for the operator.
            reason = "honeypot filled" if trap else (
                "missing, forged or expired timestamp" if age is None else f"submitted too fast ({age:.1f}s)"
            )
            logger.warning("Anti-bot rejected %s: %s", type(self).__name__, reason)
            raise forms.ValidationError(GENERIC_MESSAGE)
        return cleaned
