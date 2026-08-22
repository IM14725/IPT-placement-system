import hashlib

import filetype
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def sha256_of(file) -> str:
    digest = hashlib.sha256()
    file.seek(0)
    for chunk in file.chunks():
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def validate_upload(file) -> str:
    """Strict upload validation: size < 2MB, allowed extension, magic-byte MIME check.

    Returns the SHA-256 checksum of the file content.
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
    head = file.read(2048)
    file.seek(0)

    kind = filetype.guess(head)
    guessed_ext = kind.extension if kind else None
    if guessed_ext and guessed_ext not in {"jpg", "jpeg"} and guessed_ext != ext:
        raise ValidationError(_("File content does not match its declared type."))
    if guessed_ext in {"jpg", "jpeg"} and ext not in {"jpg", "jpeg"}:
        raise ValidationError(_("File content does not match its declared type."))
    if ext == "docx" and guessed_ext != "docx":
        raise ValidationError(_("DOCX file signature could not be verified."))

    return sha256_of(file)
