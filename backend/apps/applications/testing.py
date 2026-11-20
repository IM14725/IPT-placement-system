"""Helpers for tests that accept applicants: a real (small) PDF to upload as the acceptance letter."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def letter_pdf_bytes(text="Acceptance letter", pages=1) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    for number in range(pages):
        c.drawString(72, 760, f"{text} (page {number + 1})")
        c.showPage()
    c.save()
    return buffer.getvalue()


def sample_letter(name="letter.pdf", text="Acceptance letter", pages=1) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, letter_pdf_bytes(text, pages), content_type="application/pdf")
