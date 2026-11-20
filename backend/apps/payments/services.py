from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from apps.payments.utils import CREDIT_TXN_PREFIX, VOUCHER_TXN_PREFIX, WAIVED_TXN_PREFIX

# Platform palette (same tokens as the branded emails).
PRIMARY = HexColor("#3525CD")
PRIMARY_CONTAINER = HexColor("#4F46E5")
INK = HexColor("#191C1D")
MUTED = HexColor("#464555")
SOFT = HexColor("#6B6A7B")
LINE = HexColor("#E3E2EC")
TINT = HexColor("#EEF0FF")
ACCENT = HexColor("#F59A23")
GREEN = HexColor("#1B7F4B")


def _money(value, currency):
    return f"{currency} {Decimal(value):,.2f}"


def receipt_summary(payment) -> dict:
    """Everything a receipt shows, worked out once for the email, the PDF and the verification page.

    Handles the four ways an application can be settled: mobile money, a voucher, a cancellation credit
    and the temporary payment waiver (amount 0, nothing collected).
    """
    app = payment.application
    user = app.student.user
    currency = payment.currency
    txn = payment.gateway_txn_id or ""
    redemption = getattr(app, "voucher_redemption", None)
    discount_label = ""

    if txn.startswith(WAIVED_TXN_PREFIX):
        try:
            fee = Decimal(str((payment.callback_payload or {}).get("fee")))
        except (InvalidOperation, TypeError, ValueError):
            from apps.payments.utils import get_application_fee

            fee = get_application_fee()
        discount, paid, discount_label = fee, Decimal("0"), "Fee waived"
        badge, method, reference_txn = "FEE WAIVED", "No payment required", ""
    elif txn.startswith(CREDIT_TXN_PREFIX):
        fee = payment.amount
        discount, paid, discount_label = fee, Decimal("0"), "Re-application credit"
        badge, method, reference_txn = "COVERED BY CREDIT", "Re-application credit", ""
    elif redemption is not None:
        fee, discount, paid = redemption.fee_amount, redemption.discount_amount, payment.amount
        discount_label = f"Voucher {redemption.voucher.display_code}"
        badge = "PAID" if paid > 0 else "COVERED BY VOUCHER"
        method = payment.get_method_display() if paid > 0 else "Voucher"
        reference_txn = "" if txn.startswith(VOUCHER_TXN_PREFIX) else txn
    else:
        fee, discount, paid = payment.amount, Decimal("0"), payment.amount
        badge, method, reference_txn = "PAID", payment.get_method_display() or "-", txn

    when = timezone.localtime(payment.paid_at or payment.created_at)
    return {
        "reference": payment.reference_id,
        "date": when.strftime("%d %b %Y, %H:%M"),
        "badge": badge,
        "is_money": paid > 0,
        "student": user.get_full_name() or user.email,
        "organization": app.slot.organization.name,
        "slot": app.slot.title,
        "district": str(app.slot.district),
        "role": app.slot.role_type,
        "method": method or "-",
        "transaction": reference_txn or "-",
        "currency": currency,
        "fee": _money(fee, currency),
        "discount": _money(discount, currency) if discount > 0 else "",
        "discount_label": discount_label,
        "paid": _money(paid, currency),
    }


def _draw_logo(c, x, y, height):
    from apps.core.email_layout import LOGO_PATH

    try:
        image = ImageReader(str(LOGO_PATH))
    except Exception:  # noqa: BLE001 - a missing logo must not stop the receipt
        c.setFillColor(PRIMARY)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(x, y + height / 2, "IPT Marketplace")
        return
    w, h = image.getSize()
    c.drawImage(image, x, y, width=height * w / h, height=height, mask="auto")


def render_receipt_pdf(payment) -> bytes:
    """The PDF receipt (logo, itemised fee, QR code to verify it) as bytes; nothing is saved."""
    from apps.payments.verification import qr_png, verification_url

    data = receipt_summary(payment)
    url = verification_url(payment)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Receipt {data['reference']}")
    c.setAuthor("IPT Marketplace")
    width, height = A4
    left, right = 20 * mm, width - 20 * mm

    # Top bar, logo and title
    c.setFillColor(PRIMARY_CONTAINER)
    c.rect(0, height - 6 * mm, width, 6 * mm, stroke=0, fill=1)
    _draw_logo(c, left, height - 36 * mm, 24 * mm)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 20)
    c.drawRightString(right, height - 22 * mm, "PAYMENT RECEIPT")
    c.setFillColor(SOFT)
    c.setFont("Helvetica", 9)
    c.drawRightString(right, height - 28 * mm, "Application fee, IPT Marketplace")

    # Status badge
    c.setFillColor(GREEN if data["is_money"] else PRIMARY)
    c.roundRect(right - 52 * mm, height - 41 * mm, 52 * mm, 8 * mm, 4 * mm, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(right - 26 * mm, height - 38.4 * mm, data["badge"])

    def section(title, rows, y):
        c.setFillColor(PRIMARY)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(left, y, title.upper())
        c.setStrokeColor(LINE)
        c.setLineWidth(0.6)
        c.line(left, y - 2 * mm, right, y - 2 * mm)
        y -= 9 * mm
        for label, value in rows:
            c.setFillColor(SOFT)
            c.setFont("Helvetica", 9.5)
            c.drawString(left, y, label)
            c.setFillColor(INK)
            c.setFont("Helvetica-Bold" if label == "Receipt no." else "Helvetica", 10)
            value = str(value)
            room = right - left - 48 * mm
            if stringWidth(value, "Helvetica", 10) > room:
                while value and stringWidth(value + "...", "Helvetica", 10) > room:
                    value = value[:-1]
                value = value.rstrip() + "..."
            c.drawString(left + 48 * mm, y, value)
            y -= 7 * mm
        return y - 3 * mm

    y = height - 56 * mm
    y = section("Receipt", [
        ("Receipt no.", data["reference"]),
        ("Date", data["date"]),
        ("Payment method", data["method"]),
        ("Transaction ID", data["transaction"]),
    ], y)
    y = section("Student and placement", [
        ("Student", data["student"]),
        ("Organization", data["organization"]),
        ("Placement", data["slot"]),
        ("District", data["district"]),
        ("Role type", data["role"]),
    ], y)

    # Totals box
    box_h = 30 * mm if data["discount"] else 23 * mm
    c.setFillColor(TINT)
    c.setStrokeColor(LINE)
    c.roundRect(left, y - box_h, right - left, box_h, 3 * mm, stroke=1, fill=1)
    row_y = y - 8 * mm
    c.setFont("Helvetica", 10)
    c.setFillColor(MUTED)
    c.drawString(left + 6 * mm, row_y, "Application fee")
    c.drawRightString(right - 6 * mm, row_y, data["fee"])
    if data["discount"]:
        row_y -= 7 * mm
        c.drawString(left + 6 * mm, row_y, data["discount_label"])
        c.drawRightString(right - 6 * mm, row_y, f"- {data['discount']}")
    row_y -= 9 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(left + 6 * mm, row_y, "Total paid")
    c.drawRightString(right - 6 * mm, row_y, data["paid"])
    y -= box_h + 12 * mm

    # Verification block: QR + link
    qr_size = 38 * mm
    c.drawImage(ImageReader(BytesIO(qr_png(url))), left, y - qr_size, width=qr_size, height=qr_size)
    text_x = left + qr_size + 8 * mm
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(text_x, y - 8 * mm, "Verify this receipt")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9)
    c.drawString(text_x, y - 15 * mm, "Scan the QR code with your phone camera, or open the link below,")
    c.drawString(text_x, y - 20 * mm, "to confirm this receipt was issued by IPT Marketplace.")
    c.setFillColor(PRIMARY)
    link_size = 7.2
    while link_size > 5 and stringWidth(url, "Courier", link_size) > (right - text_x):
        link_size -= 0.2  # a long production address still fits inside the margin
    c.setFont("Courier", link_size)
    c.drawString(text_x, y - 28 * mm, url)
    c.linkURL(url, (text_x, y - 30 * mm, right, y - 26 * mm), relative=0)
    c.setFillColor(SOFT)
    c.setFont("Helvetica", 8)
    c.drawString(text_x, y - 34 * mm, f"Receipt no. {data['reference']}")

    # Footer
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1.2)
    c.line(left, 24 * mm, right, 24 * mm)
    c.setFillColor(SOFT)
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, 18 * mm, "This receipt was generated by IPT Marketplace and is valid without a signature.")
    c.drawCentredString(width / 2, 13.5 * mm, "Connecting Tanzanian university students with companies for Industrial Practical Training.")

    c.showPage()
    c.save()
    return buffer.getvalue()


def build_payment_receipt(payment):
    """Generate the PDF receipt and attach it to the record."""
    if payment.receipt_pdf:
        return payment.receipt_pdf.path

    name = f"{payment.reference_id}.pdf"
    payment.receipt_pdf.save(name, ContentFile(render_receipt_pdf(payment)), save=False)
    payment.save(update_fields=["receipt_pdf", "updated_at"])
    return payment.receipt_pdf.path
