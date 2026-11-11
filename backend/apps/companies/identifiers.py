"""Business identifiers an organization may give at registration (all optional), and the free-email check.

TIN: Tanzania Revenue Authority Taxpayer Identification Number, 9 digits, written 123-456-789.
BRELA number: the company registration number from BRELA. Formats vary by entity type, so it is only
checked for sensible characters and length; an administrator compares it with the uploaded documents.
"""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

BRELA_RE = re.compile(r"^[A-Z0-9][A-Z0-9 ./\-]{2,38}[A-Z0-9]$")

# Webmail providers: allowed (many small Tanzanian businesses use them) but flagged for the reviewer.
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "ymail.com", "rocketmail.com",
        "hotmail.com", "hotmail.co.uk", "outlook.com", "live.com", "msn.com", "icloud.com", "me.com",
        "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "zoho.com", "yandex.com",
    }
)


def normalize_tin(value):
    """Return the TIN as 123-456-789, or "" when none was given. Raises ValidationError if malformed."""
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"[\s\-.]", "", raw)
    if not (digits.isdigit() and len(digits) == 9):
        raise ValidationError(_("A TIN has 9 digits, for example 123-456-789."), code="invalid_tin")
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def normalize_brela(value):
    raw = re.sub(r"\s+", " ", (value or "").strip()).upper()
    if not raw:
        return ""
    if not BRELA_RE.match(raw):
        raise ValidationError(
            _("Enter the registration number exactly as it appears on your BRELA certificate."), code="invalid_brela"
        )
    return raw


def is_free_email(email):
    domain = (email or "").rpartition("@")[2].strip().lower()
    return bool(domain) and domain in FREE_EMAIL_DOMAINS
