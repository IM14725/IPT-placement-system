"""Acceptance letters: the organization uploads a PDF, the server stamps a verification QR and locks it,
that same file is emailed, and anyone can check a letter (or their copy of it) on a public page."""
import hashlib
import io

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import Client
from django.urls import reverse
from pypdf import PdfReader

from apps.applications import letter_services
from apps.applications.models import AcceptanceLetter
from apps.applications.services import AcceptanceError, accept_and_notify, send_acceptance_letter
from apps.applications.testing import letter_pdf_bytes, sample_letter
from apps.companies.test_dashboard_features import _client, _paid, _slot, alpha, beta, district  # noqa: F401


def _accept(app, owner, **kw):
    accept_and_notify(app, letter=kw.pop("letter", sample_letter()), actor=owner, **kw)
    app.refresh_from_db()
    return AcceptanceLetter.objects.get(application=app, is_current=True)


@pytest.fixture
def paid(alpha, district):
    org, owner = alpha
    return _paid(_slot(org, district), "stu@x.tz"), owner


# ── accepting needs a usable PDF ───────────────────────────────────────────


@pytest.mark.django_db
def test_accepting_without_a_letter_is_refused_and_nothing_changes(paid):
    app, owner = paid
    with pytest.raises(AcceptanceError):
        accept_and_notify(app, letter=None, actor=owner)
    app.refresh_from_db()
    assert not app.is_accepted and not AcceptanceLetter.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "name,content",
    [
        ("letter.docx", b"PK\x03\x04 not a pdf"),
        ("letter.pdf", b"just some text"),
        ("letter.pdf", b"%PDF-1.4 broken"),
        ("letter.png", letter_pdf_bytes()),
    ],
)
def test_only_real_pdfs_are_accepted(paid, name, content):
    app, owner = paid
    with pytest.raises(AcceptanceError):
        accept_and_notify(app, letter=SimpleUploadedFile(name, content), actor=owner)
    app.refresh_from_db()
    assert not app.is_accepted


@pytest.mark.django_db
def test_oversized_and_password_protected_pdfs_are_refused(paid, settings):
    from pypdf import PdfWriter

    app, owner = paid
    settings.MAX_UPLOAD_SIZE = 1000
    with pytest.raises(AcceptanceError, match="2 MB"):
        accept_and_notify(app, letter=sample_letter(), actor=owner)
    settings.MAX_UPLOAD_SIZE = 2 * 1024 * 1024
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(letter_pdf_bytes())))
    writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(AcceptanceError, match="password"):
        accept_and_notify(app, letter=SimpleUploadedFile("l.pdf", out.getvalue()), actor=owner)


# ── the stored, stamped, locked letter ─────────────────────────────────────


@pytest.mark.django_db
def test_the_letter_is_stamped_on_the_last_page_locked_and_fingerprinted(alpha, district):
    org, owner = alpha
    app = _paid(_slot(org, district), "two@x.tz")
    letter = _accept(app, owner, **{"letter": sample_letter(pages=2)})
    data = letter_services.read_verified(letter)
    assert data and hashlib.sha256(data).hexdigest() == letter.sha256 and letter.page_count == 2
    reader = PdfReader(io.BytesIO(data))
    assert reader.is_encrypted and reader.decrypt("")  # opens normally, but is locked
    first, last = (p.extract_text() for p in reader.pages)
    assert "Scan to verify this letter" in last and "Scan to verify this letter" not in first  # last page only
    assert letter_services.letter_reference(letter.id) in last
    assert not (reader.user_access_permissions & 8)  # modify
    assert not (reader.user_access_permissions & 16)  # copy
    assert reader.user_access_permissions & 4  # print stays allowed
    assert len(letter.source_sha256) == 64 and letter.source_sha256 != letter.sha256  # the upload and the stamped file differ


@pytest.mark.django_db
def test_the_email_carries_the_same_file_and_the_designed_card(paid, settings):
    settings.EMAIL_LOGO_URL = "https://cdn.example.tz/logo.png"
    app, owner = paid
    mail.outbox.clear()
    letter = _accept(app, owner, company_message="Report Monday")
    sent = [m for m in mail.outbox if "accepted" in m.subject.lower()][-1]
    (name, content, mimetype), = sent.attachments
    assert mimetype == "application/pdf" and content == letter_services.read_verified(letter)
    url = letter_services.verification_url(letter.id)
    assert url in sent.body and "Open the platform" in sent.body
    raw = sent.message().as_string()
    assert "Content-Type: image/" not in raw  # no inline image parts: Gmail shows no extra chips
    html = sent.alternatives[0].content if hasattr(sent.alternatives[0], "content") else sent.alternatives[0][0]
    assert "ACCEPTED" in html and "Report Monday" in html and "Open my application" in html and url in html


@pytest.mark.django_db
def test_the_student_downloads_the_stored_letter_and_a_tampered_file_is_refused(paid):
    app, owner = paid
    letter = _accept(app, owner)
    student = _client(app.student.user)
    url = reverse("student-acceptance-letter", args=[app.id])
    resp = student.get(url)
    assert resp.status_code == 200 and resp.content == letter_services.read_verified(letter)
    assert "no-store" in resp["Cache-Control"] and resp["X-Content-Type-Options"] == "nosniff"
    # someone edits the stored file on disk
    with letter.file.open("wb") as handle:
        handle.write(b"%PDF-1.4 tampered")
    assert student.get(url).status_code == 410
    assert _client(owner).get(reverse("organization-applicant-acceptance-letter", args=[app.id])).status_code == 410


@pytest.mark.django_db
def test_other_students_and_organizations_cannot_download_it(paid, alpha, beta, district):
    app, owner = paid
    _accept(app, owner)
    other = _paid(_slot(alpha[0], district, "Other"), "other@x.tz")
    assert _client(other.student.user).get(reverse("student-acceptance-letter", args=[app.id])).status_code == 404
    assert _client(beta[1]).get(reverse("organization-applicant-acceptance-letter", args=[app.id])).status_code == 404


# ── the database refuses edits ─────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
def test_a_stored_letter_cannot_be_edited_or_deleted_in_the_database(paid):
    app, owner = paid
    letter = _accept(app, owner)
    for change in ({"sha256": "0" * 64}, {"file": "other.pdf"}, {"version": 9}):
        with pytest.raises(DatabaseError), transaction.atomic():
            AcceptanceLetter.objects.filter(pk=letter.pk).update(**change)
    with pytest.raises(DatabaseError), transaction.atomic():
        AcceptanceLetter.objects.filter(pk=letter.pk).delete()
    letter.refresh_from_db()
    assert letter.sha256 != "0" * 64


# ── public verification page ───────────────────────────────────────────────


@pytest.mark.django_db
def test_verification_page_shows_masked_details_and_never_contact_data(paid):
    app, owner = paid
    letter = _accept(app, owner)
    page = Client().get(letter_services.verification_path(letter.id))
    html = page.content.decode()
    assert page.status_code == 200 and "data-letter-genuine" in html and "Genuine acceptance letter" in html
    assert "Alpha Ltd" in html and letter_services.letter_reference(letter.id) in html
    assert app.student.user.email not in html and "Open IPT Marketplace" in html
    assert page["X-Robots-Tag"] == "noindex, nofollow"


@pytest.mark.django_db
def test_forged_and_unknown_letter_links_look_the_same(paid):
    app, owner = paid
    letter = _accept(app, owner)
    forged = Client().get(reverse("letter-verify", args=[letter.id, "0" * 24]))
    import uuid

    unknown_id = uuid.uuid4()
    unknown = Client().get(reverse("letter-verify", args=[unknown_id, letter_services.signed_links.sign(letter_services.KIND, unknown_id)]))
    for resp in (forged, unknown):
        assert resp.status_code == 404 and b"data-letter-unverified" in resp.content


@pytest.mark.django_db
def test_check_a_file_tells_original_from_edited(paid):
    app, owner = paid
    letter = _accept(app, owner)
    c, path = Client(), letter_services.verification_path(letter.id)
    original = letter_services.read_verified(letter)

    def check(content):
        return c.post(path, {"file": SimpleUploadedFile("mine.pdf", content, content_type="application/pdf")}).content.decode()

    assert 'data-check-result="match"' in check(original)
    assert 'data-check-result="different"' in check(original + b"\n% edited")
    assert 'data-check-result="different"' in check(letter_pdf_bytes("Forged letter"))


# ── corrections and older acceptances ──────────────────────────────────────


@pytest.mark.django_db
def test_one_replacement_is_allowed_and_the_old_letter_says_it_was_replaced(paid):
    app, owner = paid
    first = _accept(app, owner)
    mail.outbox.clear()
    second = send_acceptance_letter(app, letter=sample_letter(text="Corrected"), actor=owner)
    assert second.version == 2 and second.is_current
    first.refresh_from_db()
    assert not first.is_current and first.superseded_at is not None
    sent = mail.outbox[-1]
    assert "Updated acceptance letter" in sent.subject and sent.attachments[0][1] == letter_services.read_verified(second)
    old_page = Client().get(letter_services.verification_path(first.id)).content.decode()
    assert "Replaced by a newer letter" in old_page
    # the old file checked against the page says it is an older version
    old_bytes = letter_services.read_verified(first)
    result = Client().post(
        letter_services.verification_path(first.id), {"file": SimpleUploadedFile("o.pdf", old_bytes)}
    ).content.decode()
    assert 'data-check-result="match"' in result  # it is the original of THIS (old) letter
    new_page = Client().post(
        letter_services.verification_path(second.id), {"file": SimpleUploadedFile("o.pdf", old_bytes)}
    ).content.decode()
    assert 'data-check-result="other_version"' in new_page
    with pytest.raises(AcceptanceError, match="once"):
        send_acceptance_letter(app, letter=sample_letter(text="Third"), actor=owner)


@pytest.mark.django_db
def test_an_application_accepted_before_letters_existed_waits_for_an_upload(paid):
    from apps.applications.services import accept_application

    app, owner = paid
    accept_application(app, company_message="old")  # the old flow: accepted, no letter
    assert _client(app.student.user).get(reverse("student-acceptance-letter", args=[app.id])).status_code == 404
    mail.outbox.clear()
    resp = _client(owner).post(
        reverse("organization-letter-upload", args=[app.id]), {"acceptance_letter": sample_letter()}
    )
    assert resp.status_code == 302
    assert AcceptanceLetter.objects.filter(application=app, version=1, is_current=True).exists()
    assert _client(app.student.user).get(reverse("student-acceptance-letter", args=[app.id])).status_code == 200
    assert mail.outbox[-1].attachments


# ── the organization's screens ─────────────────────────────────────────────


@pytest.mark.django_db
def test_accept_form_requires_the_letter_and_a_missing_one_keeps_the_applicant_waiting(paid):
    app, owner = paid
    c = _client(owner)
    page = c.get(reverse("organization-applicants", args=[app.slot_id])).content.decode()
    form = page[page.index(f'action="{reverse("organization-acceptance-sms", args=[app.id])}"') :][:1800]
    assert 'enctype="multipart/form-data"' in page and 'name="acceptance_letter"' in form and "required" in form
    c.post(reverse("organization-acceptance-sms", args=[app.id]), {"message": "hi"})
    app.refresh_from_db()
    assert not app.is_accepted
    c.post(reverse("organization-acceptance-sms", args=[app.id]), {"acceptance_letter": sample_letter()})
    app.refresh_from_db()
    assert app.is_accepted


@pytest.mark.django_db
def test_bulk_accept_no_longer_exists():
    from django.urls import NoReverseMatch

    with pytest.raises(NoReverseMatch):
        reverse("organization-bulk-accept")
