"""Acceptance-letter PDFs: validate the upload, stamp a verification QR code, lock the file.

The organization uploads its own letter (PDF only; a scan inside a PDF is fine). Before it is saved or
emailed the server

* checks it really is a PDF within the 2 MB / page limits and not password protected,
* rebuilds it page by page (drops scripts, embedded files, form actions and link annotations),
* stamps a QR code at the bottom right of the last page that opens the signed verification page,
* encrypts it with a random owner password nobody keeps, allowing printing only (no editing, copying
  or annotating), and
* returns the bytes, whose SHA-256 is kept as the letter's fingerprint.
"""
from __future__ import annotations

import io
import secrets

import filetype
import segno
from django.conf import settings
from pypdf import PdfReader, PdfWriter
from pypdf.constants import UserAccessPermissions
from reportlab.lib.colors import HexColor, white
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

MAX_PAGES = 20
QR_SIZE = 24 * mm
MARGIN = 8 * mm
TILE_PAD = 2 * mm


class LetterError(Exception):
    """The uploaded letter cannot be used (the message is safe to show the organization)."""


def read_letter_upload(upload) -> bytes:
    """The validated bytes of an uploaded letter. Raises :class:`LetterError`."""
    if upload is None:
        raise LetterError("Upload the acceptance letter as a PDF.")
    name = (getattr(upload, "name", "") or "").lower()
    if not name.endswith(".pdf"):
        raise LetterError("The acceptance letter must be a PDF file.")
    if upload.size > settings.MAX_UPLOAD_SIZE:
        raise LetterError("The acceptance letter is larger than 2 MB. Compress the PDF and try again.")
    upload.seek(0)
    data = upload.read()
    upload.seek(0)
    kind = filetype.guess(data[:8192])
    if data.lstrip()[:5] != b"%PDF-" or kind is None or kind.mime != "application/pdf":
        raise LetterError("This file is not a PDF. Upload the letter as a PDF (a scanned letter saved as PDF is fine).")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise LetterError("This PDF is password protected. Remove the password and upload it again.")
        pages = len(reader.pages)
    except LetterError:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser failure means an unusable file
        raise LetterError("This PDF could not be read. Save it again as a PDF and retry.") from exc
    if pages < 1:
        raise LetterError("This PDF has no pages.")
    if pages > MAX_PAGES:
        raise LetterError(f"The letter has {pages} pages. The most allowed is {MAX_PAGES}.")
    return data


def _draw_stamp(c, *, x, y, url, caption, reference):
    """The QR (vector, so it stays sharp when printed) on a white tile with a two-line caption."""
    matrix = [list(row) for row in segno.make(url, error="m").matrix]
    n = len(matrix) + 4  # two modules of quiet zone each side
    cell = QR_SIZE / n
    tile_w, tile_h = QR_SIZE + 2 * TILE_PAD, QR_SIZE + 2 * TILE_PAD + 7 * mm
    c.setFillColor(white)
    c.setStrokeColor(HexColor("#C8C7D4"))
    c.setLineWidth(0.4)
    c.roundRect(x, y, tile_w, tile_h, 1.5 * mm, stroke=1, fill=1)
    top = y + tile_h - TILE_PAD
    c.setFillColor(HexColor("#191C1D"))
    for r, row in enumerate(matrix):
        for col, bit in enumerate(row):
            if bit:
                c.rect(x + TILE_PAD + (col + 2) * cell, top - (r + 3) * cell, cell, cell, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 5.2)
    c.drawCentredString(x + tile_w / 2, y + 4.4 * mm, caption)
    c.setFont("Helvetica", 4.8)
    c.setFillColor(HexColor("#464555"))
    c.drawCentredString(x + tile_w / 2, y + 2 * mm, reference)
    return tile_w, tile_h


def stamp_and_lock(data: bytes, *, url: str, reference: str) -> tuple[bytes, int]:
    """Return ``(final_pdf_bytes, page_count)``; see the module docstring for what happens to the file."""
    reader = PdfReader(io.BytesIO(data), strict=False)
    writer = PdfWriter()
    for source in reader.pages:
        page = writer.add_page(source)
        if page.rotation:  # so the stamp lands bottom right of what the reader sees
            page.transfer_rotation_to_content()
        for key in ("/Annots", "/AA"):  # link/form annotations and page actions can run or redirect
            if key in page:
                del page[key]
    last = writer.pages[-1]
    box = last.mediabox
    left, bottom = float(box.left), float(box.bottom)
    width, height = float(box.width), float(box.height)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(left + width, bottom + height))
    tile_w = QR_SIZE + 2 * TILE_PAD
    _draw_stamp(
        c,
        x=left + width - MARGIN - tile_w,
        y=bottom + MARGIN,
        url=url,
        caption="Scan to verify this letter",
        reference=reference,
    )
    c.showPage()
    c.save()
    buffer.seek(0)
    last.merge_page(PdfReader(buffer).pages[0])

    writer.add_metadata(
        {
            "/Title": "Acceptance letter",
            "/Producer": "IPT Marketplace",
            "/Creator": "IPT Marketplace",
            "/Subject": f"Verify this letter: {url}",
        }
    )
    stamped = io.BytesIO()
    writer.write(stamped)

    # Second pass: encrypt a clean copy of the stamped file (encrypting the writer that did the merge
    # loses the stamp in pypdf 7). The owner password is random and never stored, so nobody can lift the
    # restrictions afterwards; printing stays allowed.
    locked = PdfWriter(clone_from=PdfReader(io.BytesIO(stamped.getvalue())))
    locked.encrypt(
        user_password="",
        owner_password=secrets.token_urlsafe(32),
        permissions_flag=UserAccessPermissions.PRINT | UserAccessPermissions.PRINT_TO_REPRESENTATION,
        algorithm="AES-256",
    )
    out = io.BytesIO()
    locked.write(out)
    return out.getvalue(), len(writer.pages)
