"""The HTML receipt email: a receipt card (logo, status badge, itemised fee, total) and a QR code that opens
the public verification page. Built on the shared branded frame; the plain-text body stays the fallback."""
from __future__ import annotations

from django.template.loader import render_to_string

from apps.core import email_layout
from apps.payments.services import receipt_summary
from apps.payments.verification import qr_table_html, verification_url

GREEN = "#1B7F4B"


def build_receipt_email(payment, subject: str) -> tuple[str, dict]:
    """``(html, inline_images)`` for the receipt email of a paid ``payment``. The QR is part of the HTML (a table)
    and the logo follows the other emails, so nothing is attached and Gmail shows no image chips."""
    r = receipt_summary(payment)
    url = verification_url(payment)
    rows = [
        ("Date", r["date"]),
        ("Student", r["student"]),
        ("Organization", r["organization"]),
        ("Placement", f"{r['slot']} ({r['district']})"),
        ("Payment method", r["method"]),
    ]
    if r["transaction"] != "-":
        rows.append(("Transaction ID", r["transaction"]))
    content = render_to_string(
        "email/receipt_content.html",
        {
            "r": r,
            "rows": rows,
            "verify_url": url,
            "qr_html": qr_table_html(url),
            "badge_color": GREEN if r["is_money"] else email_layout.PRIMARY,
            "c": {
                "primary": email_layout.PRIMARY, "ink": email_layout.INK, "muted": email_layout.MUTED,
                "soft": email_layout.SOFT, "line": email_layout.LINE, "tint": email_layout.TINT,
            },
        },
    )
    page = email_layout.render_email_html(
        subject,
        "",
        content_html=content,
        preheader=f"Receipt {r['reference']} for {r['slot']} at {r['organization']}. Scan the QR code to verify it.",
        heading="Your application receipt",
    )
    return page, {}
