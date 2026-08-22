import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.documents.models import Document
from apps.documents.validators import validate_upload


def make_png_bytes(size=1024):
    head = b"\x89PNG\r\n\x1a\n" + b"\x00" * (size - 8)
    return head


@pytest.fixture
def owner():
    return User.objects.create_user(email="doc@x.tz", password="Secret123!")


@pytest.mark.django_db
def test_oversized_file_rejected(owner):
    big = SimpleUploadedFile(
        "card.png", make_png_bytes(2 * 1024 * 1024 + 1), content_type="image/png"
    )
    with pytest.raises(ValidationError):
        Document.objects.create(
            owner=owner,
            doc_type=Document.DocType.STUDENT_ID,
            file=big,
            original_name="card.png",
        )


@pytest.mark.django_db
def test_extension_allowlist(owner):
    bad_ext = SimpleUploadedFile("evil.exe", b"MZ\x90\x00", content_type="application/octet-stream")
    with pytest.raises(ValidationError):
        validate_upload(bad_ext)


@pytest.mark.django_db
def test_mime_mismatch_rejected(owner):
    # PNG magic bytes but declared as PDF -> mismatch
    fake_pdf = SimpleUploadedFile("card.pdf", make_png_bytes(), content_type="application/pdf")
    with pytest.raises(ValidationError):
        validate_upload(fake_pdf)


@pytest.mark.django_db
def test_valid_png_accepted(owner):
    good = SimpleUploadedFile(
        "card.png", make_png_bytes(), content_type="image/png"
    )
    doc = Document.objects.create(
        owner=owner,
        doc_type=Document.DocType.STUDENT_ID,
        file=good,
        original_name="card.png",
    )
    assert doc.pk
    assert doc.size_bytes > 0
    assert len(doc.sha256) == 64