"""Provider adapters (mock-first).

Swap the internals for Beem Africa / NextSMS and SendGrid in Phase 4 without
touching the tasks. In dev these log to the console.
"""

from django.core.mail import EmailMessage
from django.conf import settings
import requests
import base64


def send_sms(phone: str, text: str) -> dict:
    api_key = getattr(settings, "BEEM_API_KEY", "")
    secret_key = getattr(settings, "BEEM_SECRET_KEY", "")
    
    if not api_key or not secret_key:
        print(f"[SMS:MOCK] -> {phone}: {text}")
        return {"status": "SENT", "provider": "mock-sms", "phone": phone}
    
    # Send via Beem Africa
    url = "https://apisms.beem.africa/v1/send"
    credentials = f"{api_key}:{secret_key}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    # Format phone number for Beem (must be in international format e.g. 2557...)
    phone_clean = "".join(filter(str.isdigit, phone))
    if phone_clean.startswith("0"):
        phone_clean = "255" + phone_clean[1:]
    
    payload = {
        "source_addr": getattr(settings, "BEEM_SENDER_ID", "IPT"),
        "schedule_time": "",
        "encoding": 0,
        "message": text,
        "recipients": [
            {"recipient_id": 1, "dest_addr": phone_clean}
        ]
    }
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    
    return {"status": "SENT", "provider": "beem-sms", "phone": phone, "response": response.json()}


def send_email(subject: str, to: list, body: str, attachments=None) -> dict:
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@iptmarketplace.com")
    email = EmailMessage(
        subject=subject,
        body=body,
        to=to,
        from_email=from_email,
    )
    
    if attachments:
        for attachment in attachments:
            if isinstance(attachment, str):
                email.attach_file(attachment)
            elif isinstance(attachment, dict):
                email.attach(
                    attachment.get("filename", "attachment"),
                    attachment.get("content", b""),
                    attachment.get("mimetype")
                )
                
    email.send(fail_silently=False)
    
    # If the default backend is the console, log it so dev knows it was mocked
    provider = "smtp" if getattr(settings, "EMAIL_HOST", "") else "mock-email"
    if provider == "mock-email":
        print(f"[EMAIL:MOCK] -> {', '.join(to)} | {subject} (Attachments: {len(attachments or [])})")
        
    return {"status": "SENT", "provider": provider}