"""Async deep file validation.

The synchronous upload path already rejects bad files quickly (<2MB, extension
allowlist, magic-byte MIME check + SHA-256) so the user gets instant feedback.
This task re-reads the stored file off the request thread and performs the
heavier checks — re-hashing, full magic-byte verification, image dimension /
decode validation — then records the result in ``scan_status``. Deep
validation therefore never blocks the web request or risks a server timeout.
"""

import hashlib

from celery import shared_task


def _sha256_of_path(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_image(head, ext):
    """Basic image decode validation: PNG/JPEG/GIF dimensions from headers."""
    import struct

    if ext == "png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n") or len(head) < 24:
            raise ValueError("Invalid PNG structure")
        width, height = struct.unpack(">II", head[16:24])
    elif ext == "jpeg":
        if not head.startswith(b"\xff\xd8"):
            raise ValueError("Invalid JPEG structure")
        i = 2
        width = height = None
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", head[i + 5 : i + 9])
                break
            seg_len = struct.unpack(">H", head[i + 2 : i + 4])[0]
            i += 2 + seg_len
        if not width or not height:
            raise ValueError("No image frame found in JPEG")
    elif ext in ("gif",):
        if not head.startswith(b"GIF87a") and not head.startswith(b"GIF89a"):
            raise ValueError("Invalid GIF structure")
        width, height = struct.unpack("<HH", head[6:10])
    else:
        return
    if width < 1 or height < 1 or width > 10000 or height > 10000:
        raise ValueError("Suspicious image dimensions")


def _deep_scan(path, declared_ext, declared_sha256):
    """Return (ok, message). Raises nothing; encodes failures as messages."""
    import filetype

    try:
        head = open(path, "rb").read(4096)
    except OSError as exc:
        return False, f"Could not read stored file: {exc}"

    actual_sha256 = _sha256_of_path(path)
    if declared_sha256 and actual_sha256 != declared_sha256:
        return False, "Stored file hash does not match the uploaded bytes"

    kind = filetype.guess(head)
    guessed_ext = kind.extension if kind else None
    ext = declared_ext.lower()
    if guessed_ext:
        if guessed_ext in {"jpg", "jpeg"}:
            if ext not in {"jpg", "jpeg"}:
                return False, "File content does not match its declared type"
        elif guessed_ext != ext:
            return False, "File content does not match its declared type"

    try:
        _validate_image(head, ext)
    except ValueError as exc:
        return False, str(exc)
    return True, ""


@shared_task(name="documents.scan")
def scan(document_id):
    """Deep-validate a stored document and persist the result."""
    from apps.documents.models import Document

    doc = Document.objects.get(id=document_id)
    if not doc.file:
        return {"document_id": document_id, "status": "ERROR", "error": "No file"}
    path = doc.file.path
    ext = doc.original_name.rsplit(".", 1)[-1].lower() if "." in doc.original_name else ""
    ok, error = _deep_scan(path, ext, doc.sha256)
    scan_status = "CLEAN" if ok else "ERROR"
    Document.objects.filter(pk=doc.pk).update(
        scan_status=scan_status,
        scan_error=error,
    )
    return {"document_id": document_id, "status": scan_status, "error": error}


@shared_task(name="documents.receipt_pdf")
def receipt_pdf(payment_id):
    """Generate the PDF receipt for a paid payment and attach it to the record."""
    from apps.payments.models import Payment
    from apps.payments.services import build_payment_receipt

    payment = Payment.objects.get(id=payment_id)
    pdf_path = build_payment_receipt(payment)
    return {"payment_id": payment_id, "pdf": pdf_path}