"""The acceptance email: a designed card, the organization's note, buttons and the letter itself attached."""
from __future__ import annotations

from django.conf import settings
from django.template.loader import render_to_string

from apps.core import email_layout

from . import letter_services


def app_url(application) -> str:
    return f"{settings.SITE_URL}/student/applications/{application.id}/next-steps/"


def build_acceptance_email(application, letter, subject: str, *, note: str = "", replacement: bool = False) -> str:
    """The HTML page of the acceptance email."""
    slot = application.slot
    user = application.student.user
    rows = [
        ("Organization", slot.organization.name),
        ("Position", slot.title),
        ("Location", f"{slot.district}, {slot.district.region}"),
    ]
    if slot.start_date:
        rows.append(("Start date", f"{slot.start_date.day} {slot.start_date:%B %Y}"))
    if slot.duration_weeks:
        rows.append(("Duration", f"{slot.duration_weeks} week{'s' if slot.duration_weeks != 1 else ''}"))
    if slot.reporting_contact:
        rows.append(("Report to", slot.reporting_contact))
    verify_url = letter_services.verification_url(letter.id)
    content = render_to_string(
        "email/acceptance_content.html",
        {
            "student": user.full_name or user.email,
            "organization": slot.organization.name,
            "slot": slot.title,
            "rows": rows,
            "note": note,
            "replacement": replacement,
            "reference": letter_services.letter_reference(letter.id),
            "app_url": app_url(application),
            "verify_url": verify_url,
            "c": {
                "primary": email_layout.PRIMARY, "ink": email_layout.INK, "muted": email_layout.MUTED,
                "soft": email_layout.SOFT, "line": email_layout.LINE, "tint": email_layout.TINT,
                "accent": email_layout.ACCENT,
            },
        },
    )
    return email_layout.render_email_html(
        subject,
        "",
        content_html=content,
        preheader=f"{slot.organization.name} accepted your application for {slot.title}. Your acceptance letter is attached.",
        heading="Your acceptance letter is updated" if replacement else "You have been accepted",
    )
