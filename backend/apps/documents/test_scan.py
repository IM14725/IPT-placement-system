import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.documents.models import Document
from apps.documents.tasks import scan

VALID_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

# JPEG magic bytes with no image frame: passes the fast upload validation but
# fails the deep decode scan.
BAD_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 512


@pytest.fixture
def _user(db):
    return User.objects.create_user(email="student@x.tz", password="Secret123!")


def test_scan_marks_valid_document_clean(_user):
    doc = Document.objects.create(
        owner=_user,
        doc_type=Document.DocType.STUDENT_ID,
        file=SimpleUploadedFile("id.png", VALID_PNG),
        original_name="id.png",
    )
    result = scan(doc.id)
    doc.refresh_from_db()
    assert result["status"] == "CLEAN"
    assert doc.scan_status == "CLEAN"
    assert doc.scan_error == ""


def test_scan_marks_corrupt_document_error(_user):
    doc = Document.objects.create(
        owner=_user,
        doc_type=Document.DocType.CV,
        file=SimpleUploadedFile("cv.jpeg", BAD_JPEG),
        original_name="cv.jpeg",
    )
    result = scan(doc.id)
    doc.refresh_from_db()
    assert result["status"] == "ERROR"
    assert doc.scan_status == "ERROR"
    assert doc.scan_error