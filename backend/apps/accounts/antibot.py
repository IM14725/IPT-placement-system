"""Keyless bot defence for the login and registration forms (no external service, no API key).

Two cheap checks that real people never trip:

* a honeypot field hidden from people but filled in by naive bots;
* a signed timestamp issued when the form is rendered: a submission that arrives faster than a
  person could type, or with a missing/forged/expired stamp, is rejected.

Both failures give the same generic message and never reveal which check fired. This sits on top of
the existing rate limits and lockouts; it does not replace them.
"""

import time

from django import forms
from django.conf import settings
from django.core.signing import BadSignature, TimestampSigner
from django.utils.translation import gettext_lazy as _

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
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}),
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
        if self.cleaned_data.get("website") or age is None or age < self.antibot_min_seconds:
            raise forms.ValidationError(GENERIC_MESSAGE)
        return cleaned
