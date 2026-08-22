"""Provider adapters (mock-first).

Swap the internals for Beem Africa / NextSMS and SendGrid in Phase 4 without
touching the tasks. In dev these log to the console.
"""

from django.core.mail import EmailMessage


def send_sms(phone: str, text: str) -> dict:
    print(f"[SMS:MOCK] -> {phone}: {text}")
    return {"status": "SENT", "provider": "mock-sms", "phone": phone}


def send_email(subject: str, to: list, body: str, attachments=None) -> dict:
    email = EmailMessage(
        subject=subject,
        body=body,
        to=to,
        attachments=attachments or [],
    )
    email.send(fail_silently=False)
    print(f"[EMAIL:MOCK] -> {', '.join(to)} | {subject}")
    return {"status": "SENT", "provider": "mock-email"}