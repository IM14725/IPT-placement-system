import pytest
from django.db import DatabaseError

from apps.accounts.models import User, UserRole
from apps.applications.models import Application, ApplicationStatus
from apps.applications.services import (
    SlotFullError,
    accept_application,
    create_application,
    finalize_verified_payment,
    get_company_visible_applications,
)
from apps.applications.tasks import expire_unpaid
from apps.companies.models import CompanyProfile
from apps.locations.models import District, Region
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
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
        district=None,
        region=None,
    )


@pytest.mark.django_db
def test_create_application_starts_pending(slot):
    student = make_student("s1@x.tz")
    app = create_application(student, slot)
    assert app.status == ApplicationStatus.PENDING
    assert not app.is_paid
    assert app.payment_deadline is not None


@pytest.mark.django_db
def test_unpaid_application_invisible_to_company(slot):
    student = make_student("s2@x.tz")
    create_application(student, slot)
    visible = get_company_visible_applications(slot)
    assert visible.count() == 0


@pytest.mark.django_db
def test_trigger_rejects_paid_without_payment(slot):
    student = make_student("s3@x.tz")
    app = create_application(student, slot)
    with pytest.raises(DatabaseError):
        app.status = ApplicationStatus.PAID
        app.save(update_fields=["status", "updated_at"])


@pytest.mark.django_db
def test_finalize_verified_payment_marks_paid(slot):
    student = make_student("s4@x.tz")
    app = create_application(student, slot)
    payment = Payment.objects.create(
        student=student,
        application=app,
        amount=15000,
        method=PaymentMethod.M_PESA,
    )
    finalize_verified_payment(payment, gateway_txn_id="GW-1")

    payment.refresh_from_db()
    app.refresh_from_db()
    assert payment.is_paid
    assert payment.status == PaymentStatus.PAID
    assert app.status == ApplicationStatus.PAID
    assert not app.is_accepted  # acceptance waits for company confirmation

    # Slot now full
    slot.refresh_from_db()
    assert slot.booked_count == 1
    assert slot.available_count == 0
    assert slot.is_full


@pytest.mark.django_db
def test_pending_application_reserves_seat(slot):
    student_a = make_student("a@x.tz")
    student_b = make_student("b@x.tz")
    app_a = create_application(student_a, slot)
    assert app_a.status == ApplicationStatus.PENDING

    slot.refresh_from_db()
    assert slot.booked_count == 1
    assert slot.available_count == 0
    assert slot.is_full

    with pytest.raises(SlotFullError):
        create_application(student_b, slot)


@pytest.mark.django_db
def test_full_slot_rejects_new_application(slot):
    student_a = make_student("c1@x.tz")
    app_a = create_application(student_a, slot)
    payment_a = Payment.objects.create(student=student_a, application=app_a, amount=15000, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment_a)

    student_b = make_student("c2@x.tz")
    with pytest.raises(SlotFullError):
        create_application(student_b, slot)


@pytest.mark.django_db
def test_paid_application_visible_to_company(slot):
    student = make_student("d@x.tz")
    app = create_application(student, slot)
    Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    finalize_verified_payment(app.payment)
    assert get_company_visible_applications(slot).count() == 1


@pytest.mark.django_db
def test_expire_unpaid_releases_slot(slot):
    from datetime import timedelta

    from django.utils import timezone

    student = make_student("e@x.tz")
    app = create_application(student, slot)
    app.payment_deadline = timezone.now() - timedelta(hours=1)
    app.save(update_fields=["payment_deadline", "updated_at"])

    result = expire_unpaid()
    app.refresh_from_db()
    slot.refresh_from_db()
    assert result["expired"] == 1
    assert app.status == ApplicationStatus.UNPAID
    assert slot.booked_count == 0
    assert slot.available_count == 1
    assert not slot.is_full


@pytest.mark.django_db
def test_accept_requires_paid(slot):
    student = make_student("f@x.tz")
    app = create_application(student, slot)
    with pytest.raises(ValueError):
        accept_application(app)


@pytest.mark.django_db
def test_accept_application_marks_accepted(slot):
    student = make_student("g@x.tz")
    app = create_application(student, slot)
    Payment.objects.create(
        student=student, application=app, amount=15000, method=PaymentMethod.M_PESA
    )
    finalize_verified_payment(app.payment)
    app.refresh_from_db()
    assert not app.is_accepted
    accept_application(app, company_message="Report Monday 8 AM")
    app.refresh_from_db()
    assert app.is_accepted
    assert app.company_message == "Report Monday 8 AM"


@pytest.mark.django_db
def test_acceptance_email_queues_confirmation(slot):
    from apps.applications.tasks import acceptance_email
    from apps.notifications.models import Message, MessageChannel, MessageStatus

    student = make_student("h@x.tz")
    app = create_application(student, slot)
    Payment.objects.create(
        student=student, application=app, amount=15000, method=PaymentMethod.M_PESA
    )
    finalize_verified_payment(app.payment)
    accept_application(app, company_message="Check your email for the acceptance letter.")

    result = acceptance_email(app.id, app.company_message)
    message = Message.objects.get(id=result["message_id"])
    assert message.channel == MessageChannel.EMAIL
    assert message.user_id == student.user_id
    assert message.status == MessageStatus.SENT
    assert "acceptance letter" in message.body.lower()
    assert "TechCorp Ltd" in message.body


@pytest.mark.django_db
def test_company_letter_preview_served_inline(slot, client, django_user_model):
    from django.core.files.uploadedfile import SimpleUploadedFile

    student = make_student("i@x.tz")
    app = create_application(student, slot)
    Payment.objects.create(
        student=student, application=app, amount=15000, method=PaymentMethod.M_PESA
    )
    finalize_verified_payment(app.payment)
    app.application_letter = SimpleUploadedFile(
        "intro_letter.pdf", b"%PDF-1.4 fake pdf content", content_type="application/pdf"
    )
    app.letter_original_name = "intro_letter.pdf"
    app.save(update_fields=["application_letter", "letter_original_name", "updated_at"])

    client.force_login(slot.company.user)
    resp = client.get(f"/company/applicants/{app.id}/letter/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp["Content-Disposition"].startswith("inline")
    assert b"".join(resp.streaming_content).startswith(b"%PDF-1.4")