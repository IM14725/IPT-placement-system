"""Storing, serving, replacing and verifying acceptance letters (see ``letter_files`` for the PDF work)."""
from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext

from apps.core import signed_links

from .letter_files import LetterError, read_letter_upload, stamp_and_lock
from .models import AcceptanceLetter

KIND = "letter-verify"


class LetterStoreError(Exception):
    """The letter could not be stored (the message is safe to show the user)."""


def letter_reference(letter_id) -> str:
    return f"IPT-L-{str(letter_id)[:8].upper()}"


def verification_path(letter_id) -> str:
    return reverse("letter-verify", args=[letter_id, signed_links.sign(KIND, letter_id)])


def verification_url(letter_id) -> str:
    return f"{settings.SITE_URL}{verification_path(letter_id)}"


def signature_is_valid(letter_id, signature) -> bool:
    return signed_links.is_valid(KIND, letter_id, signature)


def letter_filename(application) -> str:
    return f"acceptance-letter-{str(application.id)[:8]}.pdf"


def check_upload(upload) -> bytes:
    """Validate an uploaded letter before anything else is done. Raises :class:`LetterStoreError`."""
    try:
        return read_letter_upload(upload)
    except LetterError as exc:
        raise LetterStoreError(gettext(str(exc))) from exc


def store_letter(application, upload, *, actor=None, data: bytes | None = None) -> AcceptanceLetter:
    """Stamp, lock and save ``upload`` as the application's next letter version.

    The first version is current. A second version replaces it (the first is marked superseded). A third
    is refused: one correction is allowed.
    """
    data = data if data is not None else check_upload(upload)
    with transaction.atomic():
        previous = list(
            AcceptanceLetter.objects.select_for_update().filter(application=application).order_by("-version")
        )
        version = (previous[0].version + 1) if previous else 1
        if version > AcceptanceLetter.MAX_VERSIONS:
            raise LetterStoreError(gettext("This letter was already replaced once. A second correction is not allowed."))
        letter_id = uuid.uuid4()
        try:
            final, pages = stamp_and_lock(
                data, url=verification_url(letter_id), reference=letter_reference(letter_id)
            )
        except Exception as exc:  # noqa: BLE001 - never save a letter without its QR
            raise LetterStoreError(gettext("The letter could not be processed. Save it again as a PDF and retry.")) from exc
        letter = AcceptanceLetter(
            id=letter_id,
            application=application,
            version=version,
            original_name=(getattr(upload, "name", "") or "letter.pdf")[:255],
            sha256=hashlib.sha256(final).hexdigest(),
            source_sha256=hashlib.sha256(data).hexdigest(),
            page_count=pages,
            uploaded_by=actor if getattr(actor, "pk", None) else None,
            uploaded_by_label=(getattr(actor, "full_name", "") or getattr(actor, "email", "") or "")[:200],
        )
        letter.file.save(f"v{version}-{letter_id}.pdf", ContentFile(final), save=False)
        if previous:
            AcceptanceLetter.objects.filter(pk=previous[0].pk).update(is_current=False, superseded_at=timezone.now())
        letter.save()
    application._current_letter = letter
    return letter


def read_verified(letter) -> bytes | None:
    """The stored bytes, or ``None`` when the file is missing or no longer matches its fingerprint."""
    try:
        with letter.file.open("rb") as handle:
            data = handle.read()
    except (OSError, ValueError):
        return None
    return data if hashlib.sha256(data).hexdigest() == letter.sha256 else None


def matching_letter(letter, file_bytes: bytes):
    """Which version of this application's letters ``file_bytes`` is, or ``None`` for a different file."""
    digest = hashlib.sha256(file_bytes).hexdigest()
    return AcceptanceLetter.objects.filter(application_id=letter.application_id, sha256=digest).first()


def download_response(application, letter):
    """The letter as a download, or 410 when the stored file no longer matches its fingerprint."""
    from django.http import HttpResponse, HttpResponseGone

    data = read_verified(letter)
    if data is None:
        import logging

        logging.getLogger("security").error("Acceptance letter %s failed its integrity check on download", letter.id)
        return HttpResponseGone(gettext("This letter failed its security check and cannot be downloaded. Contact the organization."))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{letter_filename(application)}"'
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
