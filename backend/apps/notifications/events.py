"""The system messages an administrator can customise, and the rules their text must follow.

The senders (apps/applications/tasks.py, apps/payments/tasks.py) look a template up by ``key`` and
``channel``, so a template is only ever used when it belongs to one of these events. Each event lists
the variables its sender provides; the editor offers exactly those and rejects any other ``{name}``.
The ``default_*`` text is what is sent when no template exists, so it must stay identical to the
fallback wording in the senders.
"""

import string
from dataclasses import dataclass

from apps.notifications.models import MessageChannel


@dataclass(frozen=True)
class Event:
    key: str
    channel: str
    label: str
    trigger: str
    variables: tuple  # ((name, sample value shown in previews and test messages), ...)
    default_subject: str
    default_body: str

    @property
    def variable_names(self):
        return [name for name, _ in self.variables]

    @property
    def samples(self):
        return dict(self.variables)


EVENTS = {
    e.key: e
    for e in (
        Event(
            key="payment_received_sms",
            channel=MessageChannel.SMS,
            label="Application submitted (SMS)",
            trigger="Student's payment is confirmed",
            variables=(
                ("student_name", "Asha Juma"),
                ("organization_name", "Acme Industrial Ltd"),
                ("slot_title", "Software Engineering Intern"),
                ("app_id", "1024"),
            ),
            default_subject="",
            default_body="Application Sent! App ID: #{app_id} | Organization: {organization_name} | "
            "Slot: {slot_title}. Track it on the IPT Marketplace.",
        ),
        Event(
            key="acceptance_sms",
            channel=MessageChannel.SMS,
            label="Application accepted (SMS)",
            trigger="An organization accepts a student",
            variables=(
                ("student_name", "Asha Juma"),
                ("organization_name", "Acme Industrial Ltd"),
                ("slot_title", "Software Engineering Intern"),
            ),
            default_subject="",
            default_body="Congratulations! You have been accepted for {slot_title} at {organization_name}. "
            "Check your email for the acceptance letter or further instructions.",
        ),
        Event(
            key="acceptance_email",
            channel=MessageChannel.EMAIL,
            label="Application accepted (email)",
            trigger="An organization accepts a student",
            variables=(
                ("student_name", "Asha Juma"),
                ("organization_name", "Acme Industrial Ltd"),
                ("slot_title", "Software Engineering Intern"),
                ("app_id", "1024"),
                ("company_message", "Please report on Monday at 8am."),
                ("company_note", "\n\nNote from Acme Industrial Ltd:\nPlease report on Monday at 8am."),
            ),
            default_subject="Congratulations! You have been accepted — {slot_title}",
            default_body="Dear {student_name},\n\n"
            "Congratulations! Your application (ID: #{app_id}) for '{slot_title}' at {organization_name} "
            "has been accepted.\n\n"
            "Please open your email and check for the official acceptance letter and further instructions "
            "from the organization. Contact {organization_name} if you have any questions.{company_note}\n\n"
            "Best regards,\n{organization_name}",
        ),
        Event(
            key="receipt_email",
            channel=MessageChannel.EMAIL,
            label="Payment receipt (email)",
            trigger="Student's payment is confirmed",
            variables=(
                ("student_name", "Asha Juma"),
                ("organization_name", "Acme Industrial Ltd"),
                ("slot_title", "Software Engineering Intern"),
                ("amount", "15,000.00"),
                ("currency", "TZS"),
                ("reference_id", "IPT-2026-0001"),
                ("paid_at", "2026-11-11 10:30"),
            ),
            default_subject="Payment Receipt - {reference_id}",
            default_body="Dear {student_name},\n\n"
            "Thank you for your application to {organization_name} ({slot_title}). "
            "Your application fee of {amount} {currency} has been received.\n\n"
            "Reference: {reference_id}\n"
            "Paid on: {paid_at}\n\n"
            "Your application has been submitted and accepted. Please see the attached receipt.\n\n"
            "IPT Marketplace",
        ),
    )
}

SMS_SEGMENT = 160


def field_names(text):
    """Placeholder names used in ``text``. Raises ValueError for malformed braces."""
    return [field for _, field, _, _ in string.Formatter().parse(text or "") if field is not None]


def validate_text(event, subject, body):
    """Error messages for a template's text, empty when it is safe to save.

    Only plain ``{name}`` placeholders are allowed: no attribute access, indexing, conversions or format
    specs, and (for a known event) only the variables that event's sender actually provides. A single bad
    placeholder would otherwise make the sender skip formatting and send the raw braces to the user.
    """
    errors = []
    if not (body or "").strip():
        errors.append("The message body cannot be empty.")
    allowed = set(event.variable_names) if event else None
    for label, text in (("subject", subject), ("body", body)):
        try:
            parsed = list(string.Formatter().parse(text or ""))
        except ValueError:
            errors.append(f"The {label} has an unmatched brace. Use {{name}} for a variable, or {{{{ and }}}} for a literal brace.")
            continue
        unknown = []
        for _, field, spec, conv in parsed:
            if field is None:
                continue
            if not field.isidentifier() or spec or conv:
                errors.append(f"The {label} contains the invalid placeholder {{{field}}}. Only plain {{name}} variables are allowed.")
            elif allowed is not None and field not in allowed and field not in unknown:
                unknown.append(field)
        if unknown:
            errors.append(
                f"The {label} uses unknown variable(s): "
                + ", ".join("{" + name + "}" for name in unknown)
                + ". Available: " + ", ".join("{" + n + "}" for n in sorted(allowed)) + "."
            )
    return errors


def render_sample(event, subject, body):
    """The subject and body with this event's sample values filled in (for previews and test sends)."""
    samples = event.samples if event else {}

    def fill(text):
        try:
            return (text or "").format(**samples)
        except (KeyError, IndexError, ValueError):
            return text or ""

    return fill(subject), fill(body)
