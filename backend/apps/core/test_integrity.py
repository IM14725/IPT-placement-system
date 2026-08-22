import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.urls import reverse

from apps.accounts.models import User, UserRole
from apps.applications.models import ApplicationStatus
from apps.applications.services import (
    accept_application,
    create_application,
    finalize_verified_payment,
)
from apps.companies.models import CompanyProfile
from apps.core.immutability import verify_chain, verify_record
from apps.core.models import AuditLog, IntegrityRecord
from apps.documents.models import Document
from apps.locations.models import District, Region
from apps.payments.models import Payment, PaymentMethod
from apps.slots.models import Slot
from apps.students.models import StudentProfile, VerificationStatus


@pytest.fixture
def region():
    return Region.objects.create(name="Dar es Salaam", slug="dar-es-salaam")


@pytest.fixture
def district(region):
    return District.objects.create(name="Kinondoni", region=region)


@pytest.fixture
def company_user():
    return User.objects.create_user(
        email="company@x.tz", password="Secret123!", role=UserRole.COMPANY
    )


@pytest.fixture
def company(company_user):
    return CompanyProfile.objects.create(
        user=company_user,
        name="TechCorp Ltd",
        industry="Technology",
        verification_status=VerificationStatus.APPROVED,
    )


@pytest.fixture
def slot(company, district):
    return Slot.objects.create(
        company=company,
        title="Software Engineering Intern",
        industry="Technology",
        role_type="Internship",
        district=district,
        capacity=1,
    )


def make_student(email):
    user = User.objects.create_user(email=email, password="Secret123!", role=UserRole.STUDENT)
    return StudentProfile.objects.create(
        user=user,
        university="University of Dar es Salaam",
        course="Computer Science",
        current_year=3,
        verification_status=VerificationStatus.APPROVED,
    )


def make_paid_application(student, slot):
    app = create_application(student, slot)
    payment = Payment.objects.create(
        student=student, application=app, amount=15000, method=PaymentMethod.M_PESA
    )
    finalize_verified_payment(payment, gateway_txn_id="GW-INTEG")
    app.refresh_from_db()
    return app


@pytest.mark.django_db
def test_audit_log_update_and_delete_rejected():
    log = AuditLog.objects.create(
        actor_label="Admin", action="Settings Updated", module="Platform Configuration"
    )
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            log.description = "changed"
            log.save(update_fields=["description"])
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            log.delete()


@pytest.mark.django_db
def test_paid_payment_financial_edit_rejected(slot):
    student = make_student("p@x.tz")
    app = make_paid_application(student, slot)
    payment = app.payment
    assert payment.is_paid
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            payment.amount = 25000
            payment.save()
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            payment.status = "FAILED"
            payment.save(update_fields=["status", "updated_at"])


@pytest.mark.django_db
def test_paid_payment_delete_rejected(slot):
    student = make_student("p2@x.tz")
    app = make_paid_application(student, slot)
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            app.payment.delete()


@pytest.mark.django_db
def test_receipt_pdf_update_allowed_after_paid(slot):
    student = make_student("p3@x.tz")
    app = make_paid_application(student, slot)
    payment = app.payment
    payment.receipt_pdf = "receipts/foo.pdf"
    payment.save(update_fields=["receipt_pdf", "updated_at"])
    payment.refresh_from_db()
    assert payment.receipt_pdf.name == "receipts/foo.pdf"


@pytest.mark.django_db
def test_paid_application_delete_rejected(slot):
    student = make_student("a1@x.tz")
    app = make_paid_application(student, slot)
    assert not app.is_accepted
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            app.delete()


@pytest.mark.django_db
def test_accepted_application_edit_and_delete_rejected(slot):
    student = make_student("a2@x.tz")
    app = make_paid_application(student, slot)
    accept_application(app, company_message="Report Monday 8 AM")
    app.refresh_from_db()
    assert app.is_accepted
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            app.status = ApplicationStatus.UNPAID
            app.save(update_fields=["status", "updated_at"])
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            app.delete()


@pytest.mark.django_db
def test_verified_document_update_and_delete_rejected():
    owner = User.objects.create_user(email="doc@x.tz", password="Secret123!", role=UserRole.STUDENT)
    doc = Document.objects.create(
        owner=owner,
        doc_type=Document.DocType.CV,
        file=SimpleUploadedFile("cv.pdf", b"%PDF-1.4 doc", content_type="application/pdf"),
        original_name="cv.pdf",
        is_verified=True,
    )
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            doc.original_name = "tampered.pdf"
            doc.save(update_fields=["original_name"])
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            doc.delete()


@pytest.mark.django_db
def test_tampered_document_returns_410(client):
    owner = User.objects.create_user(email="doc2@x.tz", password="Secret123!", role=UserRole.STUDENT)
    doc = Document.objects.create(
        owner=owner,
        doc_type=Document.DocType.CV,
        file=SimpleUploadedFile("cv.pdf", b"%PDF-1.4 good", content_type="application/pdf"),
        original_name="cv.pdf",
    )
    with open(doc.file.path, "wb") as f:
        f.write(b"%PDF-1.4 EVIL CONTENT")
    client.force_login(owner)
    resp = client.get(reverse("document-view", args=[doc.id]))
    assert resp.status_code == 410


@pytest.mark.django_db
def test_payment_and_application_saves_seal_ledger(slot):
    student = make_student("s1@x.tz")
    app = create_application(student, slot)
    assert IntegrityRecord.objects.filter(record_type="APPLICATION", record_id=app.id).exists()
    payment = Payment.objects.create(
        student=student, application=app, amount=15000, method=PaymentMethod.M_PESA
    )
    assert IntegrityRecord.objects.filter(record_type="PAYMENT", record_id=payment.id).exists()
    finalize_verified_payment(payment, gateway_txn_id="GW-X")
    assert IntegrityRecord.objects.filter(
        record_type="PAYMENT", record_id=payment.id, payload__status="PAID"
    ).exists()


@pytest.mark.django_db
def test_verify_record_detects_changed_fields(slot):
    from apps.core.signals import _application_fields

    student = make_student("s2@x.tz")
    app = create_application(student, slot)
    rec = IntegrityRecord.objects.filter(record_type="APPLICATION", record_id=app.id).order_by("-id").first()
    assert verify_record(IntegrityRecord, rec, _application_fields(app))
    tampered = _application_fields(app)
    tampered["status"] = "PAID"
    assert not verify_record(IntegrityRecord, rec, tampered)


@pytest.mark.django_db
def test_verify_chain_detects_break(slot):
    student = make_student("s3@x.tz")
    make_paid_application(student, slot)
    first = IntegrityRecord.objects.order_by("id").first()
    IntegrityRecord.objects.filter(id=first.id).update(record_hash="deadbeef" * 8)
    issues = verify_chain(IntegrityRecord)
    assert issues


@pytest.mark.django_db
def test_apply_sets_letter_sha256(client, slot):
    from apps.payments.models import Payment

    student = make_student("s4@x.tz")
    client.force_login(student.user)
    resp = client.post(
        reverse("student-apply", args=[slot.id]),
        {
            "application_letter": SimpleUploadedFile(
                "intro.pdf", b"%PDF-1.4 letter", content_type="application/pdf"
            )
        },
    )
    assert resp.status_code == 302
    pay_id = int(resp["Location"].strip("/").split("/")[-1])
    app = Payment.objects.get(id=pay_id).application
    assert app.letter_sha256
    rec = IntegrityRecord.objects.filter(record_type="APPLICATION", record_id=app.id).order_by("-id").first()
    assert rec.payload["letter_sha256"] == app.letter_sha256