"""Education levels per the Tanzania Commission for Universities (TCU).

The Tanzania National Qualifications Framework (TNQF/UQF) places higher
education awards on a 10-level scale. IPT matching uses levels 4-10:

    Level 4  Certificate (Basic Technician Certificate, NTA 4)
    Level 5  Technician Certificate (NTA 5)
    Level 6  Ordinary Diploma (NTA 6)
    Level 7  Higher Diploma / Higher Certificate
    Level 8  Bachelor's Degree
    Level 9  Master's Degree (incl. Postgraduate Certificate/Diploma)
    Level 10 Doctorate (PhD)

Source: TCU University Qualifications Framework (UQF), Tables 1-3.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class EducationLevel(models.IntegerChoices):
    CERTIFICATE = 4, _("Level 4 — Certificate")
    TECHNICIAN_CERTIFICATE = 5, _("Level 5 — Technician Certificate")
    ORDINARY_DIPLOMA = 6, _("Level 6 — Ordinary Diploma")
    HIGHER_DIPLOMA = 7, _("Level 7 — Higher Diploma")
    BACHELORS = 8, _("Level 8 — Bachelor's Degree")
    MASTERS = 9, _("Level 9 — Master's Degree")
    DOCTORATE = 10, _("Level 10 — Doctorate (PhD)")


def education_level_choices():
    """Choices for form/filter dropdowns (value, label) in TCU order."""
    return list(EducationLevel.choices)


def education_level_label(value):
    """Short label for cards/tables, e.g. 8 -> "Bachelor's"."""
    short = {
        EducationLevel.CERTIFICATE: _("Certificate"),
        EducationLevel.TECHNICIAN_CERTIFICATE: _("Technician Certificate"),
        EducationLevel.ORDINARY_DIPLOMA: _("Ordinary Diploma"),
        EducationLevel.HIGHER_DIPLOMA: _("Higher Diploma"),
        EducationLevel.BACHELORS: _("Bachelor's"),
        EducationLevel.MASTERS: _("Master's"),
        EducationLevel.DOCTORATE: _("PhD"),
    }
    try:
        return str(short[EducationLevel(value)])
    except (KeyError, ValueError):
        return ""
