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
    assert doc.mime_type == "image/png"


@pytest.mark.django_db
def test_polyglot_or_html_disguised_as_pdf_rejected(owner):
    # HTML/JS payload with .pdf extension and forged text/html content_type
    evil_html = SimpleUploadedFile(
        "exploit.pdf",
        b"<html><script>alert(document.cookie)</script></html>",
        content_type="text/html",
    )
    with pytest.raises(ValidationError):
        validate_upload(evil_html)


@pytest.mark.django_db
def test_client_content_type_ignored_in_favor_of_magic_bytes(owner):
    # Valid PNG bytes uploaded with a forged text/html Content-Type
    spoofed = SimpleUploadedFile(
        "card.png", make_png_bytes(), content_type="text/html"
    )
    doc = Document.objects.create(
        owner=owner,
        doc_type=Document.DocType.STUDENT_ID,
        file=spoofed,
        original_name="card.png",
    )
    assert doc.mime_type == "image/png"


@pytest.mark.django_db
def test_view_document_sets_security_headers_and_blocks_error_scan(owner, client):
    from django.urls import reverse

    good = SimpleUploadedFile(
        "card.png", make_png_bytes(), content_type="text/html"
    )
    doc = Document.objects.create(
        owner=owner,
        doc_type=Document.DocType.STUDENT_ID,
        file=good,
        original_name="card.png",
    )
    client.force_login(owner)
    resp = client.get(reverse("document-view", args=[doc.id]))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in resp["Content-Security-Policy"]

    # If the async deep scan flags the file as ERROR, serving is blocked with 410 Gone
    Document.objects.filter(pk=doc.pk).update(scan_status="ERROR", scan_error="Invalid PNG structure")
    resp_err = client.get(reverse("document-view", args=[doc.id]))
    assert resp_err.status_code == 410

