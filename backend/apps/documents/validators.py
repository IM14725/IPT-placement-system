import hashlib

import filetype
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

SAFE_MIMES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

INLINE_SAFE_MIMES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
}


def sha256_of(file) -> str:
    digest = hashlib.sha256()
    file.seek(0)
    for chunk in file.chunks():
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def detect_mime_and_ext(head: bytes):
    """Detect (extension, mime_type) strictly from magic bytes, or (None, None)."""
    kind = filetype.guess(head)
    if kind is None:
        return None, None
    return kind.extension, kind.mime


def validate_upload(file) -> str:
    """Strict upload validation: size < 2MB, allowed extension, magic-byte MIME check.

    Returns the SHA-256 checksum of the file content and attaches ``_detected_mime``
    to ``file`` from server-side magic bytes (ignoring client Content-Type).
    """
    if file.size > settings.MAX_UPLOAD_SIZE:
        raise ValidationError(
            _("File exceeds the 2MB limit (%(size)s bytes).")
            % {"size": file.size}
        )

    ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
    if ext not in settings.ALLOWED_DOC_EXTENSIONS:
        raise ValidationError(_("Extension '%(ext)s' is not allowed.") % {"ext": ext})

    file.seek(0)
    head = file.read(8192)
    file.seek(0)

    guessed_ext, guessed_mime = detect_mime_and_ext(head)
    if guessed_ext is None:
        if ext == "docx":
            raise ValidationError(_("DOCX file signature could not be verified."))
        raise ValidationError(_("File signature could not be verified or does not match its declared type."))

    if guessed_ext in {"jpg", "jpeg"}:
        if ext not in {"jpg", "jpeg"}:
            raise ValidationError(_("File content does not match its declared type."))
    elif guessed_ext != ext:
        if ext == "docx":
            raise ValidationError(_("DOCX file signature could not be verified."))
        raise ValidationError(_("File content does not match its declared type."))

    file._detected_mime = guessed_mime or SAFE_MIMES.get(ext, "application/octet-stream")
    return sha256_of(file)


def build_safe_file_response(file_field, filename: str, declared_mime: str = ""):
    """Return a hardened FileResponse with server-verified MIME, CSP sandbox, and nosniff."""
    from pathlib import Path
    from django.http import FileResponse

    safe_name = Path(filename or "document").name
    ext = Path(safe_name).suffix.lower().lstrip(".")

    fh = file_field.open("rb")
    head = fh.read(8192)
    fh.seek(0)

    _, detected_mime = detect_mime_and_ext(head)
    content_type = detected_mime or (
        declared_mime if declared_mime in SAFE_MIMES.values() else SAFE_MIMES.get(ext, "application/octet-stream")
    )
    as_attachment = content_type not in INLINE_SAFE_MIMES

    response = FileResponse(
        fh,
        as_attachment=as_attachment,
        filename=safe_name,
        content_type=content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


