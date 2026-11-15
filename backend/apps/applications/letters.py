"""Acceptance letter PDF.

Built on demand from the application's own data, never stored: an accepted application is
write-protected by a database trigger, so a file cannot be attached to it after acceptance. The same
application therefore always produces the same letter.
"""

from io import BytesIO

from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas


def letter_filename(application):
    return f"acceptance-letter-{str(application.id)[:8]}.pdf"


def build_acceptance_letter(application) -> bytes:
    """Return the acceptance letter for an accepted application as PDF bytes."""
    if not application.is_accepted:
        raise ValueError("Only accepted applications have an acceptance letter.")

    slot = application.slot
    organization = slot.organization
    student = application.student
    user = student.user
    name = user.full_name or user.email
    accepted_on = timezone.localtime(application.updated_at).strftime("%d %B %Y")

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.setTitle(f"Acceptance letter - {name}")
    width, height = A4
    left = 22 * mm
    usable = width - 2 * left
    y = height - 25 * mm

    def line(text, font="Helvetica", size=11, gap=6.5):
        nonlocal y
        c.setFont(font, size)
        for part in simpleSplit(str(text), font, size, usable) or [""]:
            if y < 25 * mm:
                c.showPage()
                y = height - 25 * mm
                c.setFont(font, size)
            c.drawString(left, y, part)
            y -= gap * mm

    def space(mm_gap=4):
        nonlocal y
        y -= mm_gap * mm

    line(organization.name, "Helvetica-Bold", 16, 8)
    contact = " | ".join(
        part for part in [organization.address or organization.street, organization.phone, organization.email] if part
    )
    if contact:
        line(contact, "Helvetica", 9, 5)
    c.setLineWidth(0.6)
    c.line(left, y + 2 * mm, width - left, y + 2 * mm)
    space(6)

    line(accepted_on)
    line(f"Ref: IPT/{str(application.id)[:8].upper()}", "Helvetica", 10)
    space()

    line(name, "Helvetica-Bold")
    if student.university:
        line(str(student.university))
    if student.student_id:
        line(f"Registration number: {student.student_id}")
    space()

    line("RE: ACCEPTANCE FOR INDUSTRIAL PRACTICAL TRAINING", "Helvetica-Bold", 12, 8)
    space(2)
    line(f"Dear {name},")
    space(2)
    line(
        f"We are pleased to inform you that your application for Industrial Practical Training "
        f"has been accepted. You have been offered a place as follows:"
    )
    space(2)

    details = [
        ("Position", slot.title),
        ("Role", slot.role_type),
        ("Department", slot.department or "-"),
        ("Location", f"{slot.district}, {slot.district.region}"),
        ("Course", student.course),
    ]
    if slot.stipend_available and slot.stipend_amount:
        details.append(("Monthly stipend", f"TZS {slot.stipend_amount:,.2f}"))
    if slot.start_date:
        details.append(("Start date", f"{slot.start_date.day} {slot.start_date:%B %Y}"))
    if slot.duration_weeks:
        weeks = f"{slot.duration_weeks} week{'s' if slot.duration_weeks != 1 else ''}"
        if slot.end_date:
            weeks += f" (until about {slot.end_date.day} {slot.end_date:%B %Y})"
        details.append(("Duration", weeks))
    if slot.reporting_contact:
        details.append(("Report to", slot.reporting_contact))
    for label, value in details:
        c.setFont("Helvetica-Bold", 11)
        c.drawString(left, y, f"{label}:")
        c.setFont("Helvetica", 11)
        for index, part in enumerate(simpleSplit(str(value), "Helvetica", 11, usable - 42 * mm) or [""]):
            c.drawString(left + 42 * mm, y, part)
            y -= 6.5 * mm
    space(2)

    if application.company_message:
        line("Instructions from the organization:", "Helvetica-Bold")
        for paragraph in application.company_message.splitlines():
            line(paragraph)
        space(2)

    line(
        "Please bring this letter, your student identity card and your university introduction "
        "letter when you report. Contact us using the details above if you have any questions."
    )
    space(4)
    line("Yours sincerely,")
    space(6)
    line(organization.name, "Helvetica-Bold")
    space(8)

    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        left,
        15 * mm,
        f"Issued through IPT Marketplace. Application ID {application.id}. Accepted on {accepted_on}.",
    )
    c.showPage()
    c.save()
    return buffer.getvalue()
