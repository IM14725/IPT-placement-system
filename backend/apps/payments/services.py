from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def build_payment_receipt(payment):
    """Generate a PDF receipt for a paid payment and attach it to the record."""
    if payment.receipt_pdf:
        return payment.receipt_pdf.path

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    styles = getSampleStyleSheet()

    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, height - 25 * mm, "IPT Marketplace")
    c.setFont("Helvetica", 12)
    c.drawString(20 * mm, height - 35 * mm, "Application Fee Receipt")

    y = height - 55 * mm
    c.setFont("Helvetica", 10)
    rows = [
        ("Receipt Reference", payment.reference_id),
        ("Date", timezone.localtime(payment.paid_at or payment.created_at).strftime("%Y-%m-%d %H:%M")),
        ("Amount", f"{payment.amount:,.2f} {payment.currency}"),
        ("Payment Method", payment.get_method_display()),
        ("Gateway Transaction", payment.gateway_txn_id or "-"),
        ("Status", "PAID"),
    ]
    for label, value in rows:
        c.drawString(20 * mm, y, label)
        c.drawString(90 * mm, y, value)
        y -= 8 * mm

    app = payment.application
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Placement Details")
    y -= 6 * mm
    c.setFont("Helvetica", 10)
    details = [
        ("Student", str(app.student.user.get_full_name() or app.student.user.email)),
        ("Company", app.slot.company.name),
        ("Slot", app.slot.title),
        ("District", str(app.slot.district)),
        ("Role", app.slot.role_type),
    ]
    for label, value in details:
        c.drawString(20 * mm, y, label)
        c.drawString(90 * mm, y, value[:70])
        y -= 8 * mm

    c.showPage()
    c.save()

    name = f"{payment.reference_id}.pdf"
    payment.receipt_pdf.save(name, ContentFile(buffer.getvalue()), save=False)
    payment.save(update_fields=["receipt_pdf", "updated_at"])
    return payment.receipt_pdf.path