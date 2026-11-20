"""Fee vouchers: pricing, restrictions, usage caps and their release, the apply flow, the registration
reference, the database guarantees, the admin console and the ledger split."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.applications.models import Application, ApplicationStatus
from apps.applications.services import create_application, finalize_verified_payment
from apps.companies.test_dashboard_features import _make_org
from apps.core.models import AuditLog
from apps.locations.models import District, Region
from apps.notifications.models import Notification
from apps.payments.models import Payment, PaymentMethod
from apps.payments.utils import get_application_fee
from apps.slots.models import Slot
from apps.students.models import University
from apps.students.test_dashboard_features import _student
from apps.vouchers import services
from apps.vouchers.models import (
    CodeMode,
    DiscountType,
    FeeVoucher,
    SignupReference,
    VoucherBatch,
    VoucherRedemption,
)

FEE = Decimal("15000")
PW = "Secret123!x"


@pytest.fixture
def places(db):
    dar = Region.objects.create(name="Dar es Salaam", slug="dar")
    mwanza = Region.objects.create(name="Mwanza", slug="mwanza")
    return {
        "dar": dar,
        "mwanza": mwanza,
        "kinondoni": District.objects.create(name="Kinondoni", region=dar),
        "ilala": District.objects.create(name="Ilala", region=dar),
    }


@pytest.fixture
def org(db):
    return _make_org("Alpha Ltd", "alpha")[0]


def _slot(org, district, title="Intern", **extra):
    fields = dict(organization=org, title=title, industry="Tech", role_type="Internship", district=district, capacity=5)
    fields.update(extra)
    return Slot.objects.create(**fields)


def _batch(**kw):
    fields = dict(name="Promo", discount_type=DiscountType.PERCENT, discount_value=Decimal("50"))
    fields.update(kw)
    return VoucherBatch.objects.create(**fields)


def _voucher(batch=None, code="PROMO2026", max_uses=1):
    return FeeVoucher.objects.create(batch=batch or _batch(), code=code, max_uses=max_uses)


def _fresh_code():
    import uuid

    return "T" + uuid.uuid4().hex[:10].upper()


def _letter():
    return SimpleUploadedFile("intro.pdf", b"%PDF-1.4 letter", content_type="application/pdf")


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _apply(client, slot, **extra):
    data = {"application_letter": _letter(), "accept_rules": "on"}
    data.update(extra)
    return client.post(reverse("student-apply", args=[slot.id]), data)


def _reserve(code, student, slot, fee=FEE):
    app = create_application(student, slot)
    return app, services.reserve(code, profile=student, slot=slot, application=app, fee=fee)


# ── Codes and pricing ───────────────────────────────────────────────────────


def test_normalize_code_ignores_case_spaces_and_dashes():
    assert services.normalize_code(" ipt-7k3f 9qxm ") == "IPT7K3F9QXM"
    assert services.normalize_code(None) == ""


@pytest.mark.django_db
def test_generated_codes_are_unique_readable_and_displayed_in_groups():
    batch = _batch()
    codes = services.create_codes(batch, 200)
    assert len(codes) == 200 and len({c.code for c in codes}) == 200
    sample = codes[0]
    assert sample.code.startswith("IPT") and len(sample.code) == 11
    assert not set("01OI") & set(sample.code[3:])
    assert sample.display_code == f"IPT-{sample.code[3:7]}-{sample.code[7:]}"
    assert FeeVoucher(code="WELCOME2026").display_code == "WELCOME2026"


@pytest.mark.parametrize(
    "dtype, value, expected",
    [
        (DiscountType.PERCENT, "50", ("7500", "7500")),
        (DiscountType.PERCENT, "33.33", ("5000", "10000")),  # whole shillings
        (DiscountType.PERCENT, "100", ("15000", "0")),
        (DiscountType.FIXED, "2000", ("2000", "13000")),
        (DiscountType.FIXED, "99999", ("15000", "0")),  # never below zero
        (DiscountType.WAIVER, "0", ("15000", "0")),
    ],
)
def test_compute_discount(dtype, value, expected):
    batch = VoucherBatch(discount_type=dtype, discount_value=Decimal(value))
    discount, charged = services.compute_discount(batch, FEE)
    assert (str(discount), str(charged)) == expected


# ── Reserving, caps and release ─────────────────────────────────────────────


@pytest.mark.django_db
def test_reserve_records_the_price_and_an_audit_event(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    _voucher()
    app, red = _reserve("promo-2026", student, slot)
    assert (red.fee_amount, red.discount_amount, red.amount_charged) == (FEE, Decimal("7500"), Decimal("7500"))
    assert VoucherRedemption.objects.get(application=app) == red
    assert AuditLog.objects.filter(action="Voucher Applied", module="Vouchers").exists()


@pytest.mark.django_db
def test_a_code_cannot_be_used_more_than_max_uses_and_the_use_comes_back(places, org):
    first, second = _student(places, "a@x.tz"), _student(places, "b@x.tz")
    slot = _slot(org, places["kinondoni"])
    _voucher(max_uses=1)
    app, _ = _reserve("PROMO2026", first, slot)
    with pytest.raises(services.VoucherError, match="maximum number of times"):
        _reserve("PROMO2026", second, slot)
    # The first student's seat expires unpaid: the use is free again with no other write.
    Application.objects.filter(pk=app.pk).update(status=ApplicationStatus.UNPAID)
    _, red = _reserve("PROMO2026", second, slot)
    assert red.student == second


@pytest.mark.django_db
def test_shared_campaign_code_has_a_total_cap(places, org):
    slot = _slot(org, places["kinondoni"])
    _voucher(max_uses=2)
    for i in range(2):
        _reserve("PROMO2026", _student(places, f"s{i}@x.tz"), slot)
    with pytest.raises(services.VoucherError):
        _reserve("PROMO2026", _student(places, "late@x.tz"), slot)


@pytest.mark.django_db
def test_per_student_limit_and_single_waiver(places, org):
    student = _student(places, "a@x.tz")
    one, two = _slot(org, places["kinondoni"], "One"), _slot(org, places["kinondoni"], "Two")
    batch = _batch(per_student_limit=2)
    _voucher(batch, "HALF1")
    _voucher(batch, "HALF2")
    _reserve("HALF1", student, one)
    _reserve("HALF2", student, two)  # second use allowed (limit 2)
    third = _slot(org, places["kinondoni"], "Three")
    _voucher(batch, "HALF3")
    with pytest.raises(services.VoucherError, match="already used this offer"):
        _reserve("HALF3", student, third)

    waivers = _batch(name="Waiver", discount_type=DiscountType.WAIVER, per_student_limit=5)
    _voucher(waivers, "FREE1")
    _voucher(waivers, "FREE2")
    other = _student(places, "w@x.tz")
    _reserve("FREE1", other, one)
    with pytest.raises(services.VoucherError, match="one is allowed per student"):
        _reserve("FREE2", other, two)


@pytest.mark.django_db
def test_budget_cap_stops_the_batch(places, org):
    slot = _slot(org, places["kinondoni"])
    batch = _batch(budget_tzs=Decimal("10000"))  # one 7,500 discount fits, the second would pass 10,000
    _voucher(batch, "ONE")
    _voucher(batch, "TWO")
    _reserve("ONE", _student(places, "a@x.tz"), slot)
    with pytest.raises(services.VoucherError, match="run out of funds"):
        _reserve("TWO", _student(places, "b@x.tz"), slot)


@pytest.mark.django_db
def test_unknown_revoked_paused_expired_and_scheduled_codes_are_refused(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    with pytest.raises(services.VoucherError, match="not valid"):
        services.quote("NOPE1234", profile=student, slot=slot, fee=FEE)
    voucher = _voucher()
    services.quote("PROMO2026", profile=student, slot=slot, fee=FEE)
    voucher.is_active = False
    voucher.save()
    with pytest.raises(services.VoucherError, match="not valid"):
        services.quote("PROMO2026", profile=student, slot=slot, fee=FEE)
    voucher.is_active = True
    voucher.save()
    batch = voucher.batch
    for field, value, message in (
        ("is_active", False, "not valid"),
        ("expires_at", timezone.now() - timedelta(days=1), "expired"),
        ("starts_at", timezone.now() + timedelta(days=1), "not active yet"),
    ):
        VoucherBatch.objects.filter(pk=batch.pk).update(is_active=True, expires_at=None, starts_at=None)
        VoucherBatch.objects.filter(pk=batch.pk).update(**{field: value})
        with pytest.raises(services.VoucherError, match=message):
            services.quote("PROMO2026", profile=student, slot=slot, fee=FEE)


# ── Restrictions ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_student_restrictions(places, org):
    udsm = University.objects.get_or_create(name="UDSM")[0]
    other_uni = University.objects.create(name="SUA")
    student = _student(places, "s1@x.tz")  # UDSM, Computer Science, Dar / Kinondoni
    slot = _slot(org, places["kinondoni"])

    def blocker(batch):
        voucher = FeeVoucher.objects.create(batch=batch, code=_fresh_code())
        return services.voucher_blocker(voucher, user=student.user, profile=student, slot=slot)

    batch = _batch(name="uni")
    batch.universities.add(other_uni)
    assert "universities" in blocker(batch)
    batch.universities.add(udsm)
    assert blocker(batch) == ""

    assert "courses" in blocker(_batch(name="course", courses=["Law"]))
    assert blocker(_batch(name="course ok", courses=["computer science"])) == ""

    region = _batch(name="region")
    region.regions.add(places["mwanza"])
    assert "regions" in blocker(region)
    region.regions.add(places["dar"])
    assert blocker(region) == ""

    district = _batch(name="district")
    district.districts.add(places["ilala"])
    assert "districts" in blocker(district)

    assert "specific students" in blocker(_batch(name="named", allowed_emails=["someone@x.tz"]))
    assert blocker(_batch(name="named ok", allowed_emails=["S1@x.tz"])) == ""

    assert "registered during" in blocker(_batch(name="old", signup_to=timezone.now() - timedelta(days=1)))
    assert "registered during" in blocker(_batch(name="future", signup_from=timezone.now() + timedelta(days=1)))
    assert blocker(_batch(name="window", signup_from=timezone.now() - timedelta(days=1), signup_to=timezone.now() + timedelta(days=1))) == ""


@pytest.mark.django_db
def test_slot_restrictions(places, org):
    other_org = _make_org("Beta Ltd", "beta")[0]
    student = _student(places, "s1@x.tz")
    slot = _slot(org, places["kinondoni"], start_date=date.today() + timedelta(days=30))
    voucher_for = lambda batch: FeeVoucher.objects.create(batch=batch, code=_fresh_code())  # noqa: E731

    def blocker(batch):
        return services.voucher_blocker(voucher_for(batch), user=student.user, profile=student, slot=slot)

    by_org = _batch(name="org")
    by_org.organizations.add(other_org)
    assert "does not apply" in blocker(by_org)
    by_org.organizations.add(org)
    assert blocker(by_org) == ""

    by_slot = _batch(name="slot")
    by_slot.slots.add(_slot(org, places["kinondoni"], "Other slot"))
    assert "does not apply" in blocker(by_slot)
    by_slot.slots.add(slot)
    assert blocker(by_slot) == ""

    by_district = _batch(name="district")
    by_district.slot_districts.add(places["ilala"])
    assert "does not apply" in blocker(by_district)

    assert "does not apply" in blocker(_batch(name="early", slot_start_to=date.today() + timedelta(days=10)))
    assert "does not apply" in blocker(_batch(name="late", slot_start_from=date.today() + timedelta(days=60)))
    assert blocker(_batch(name="in window", slot_start_from=date.today(), slot_start_to=date.today() + timedelta(days=60))) == ""
    # A slot with no start date cannot satisfy a start-date restriction.
    undated = _slot(org, places["kinondoni"], "Undated")
    batch = _batch(name="dated", slot_start_from=date.today())
    assert "does not apply" in services.voucher_blocker(voucher_for(batch), user=student.user, profile=student, slot=undated)


# ── The apply page ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_apply_with_a_percentage_code_charges_the_discounted_fee(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    _voucher()
    c = _client(student.user)
    resp = _apply(c, slot, voucher_code="promo-2026")
    app = Application.objects.get(student=student)
    payment = app.payment
    assert resp.status_code == 302 and resp.url == reverse("student-payment", args=[payment.id])
    assert app.status == ApplicationStatus.PENDING
    assert payment.amount == Decimal("7500") and not payment.is_paid
    page = c.get(resp.url).content.decode()
    assert "PROMO2026" in page and "7,500" in page and "15,000" in page


@pytest.mark.django_db
def test_full_waiver_skips_the_gateway_and_marks_the_application_paid(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    _voucher(_batch(discount_type=DiscountType.WAIVER), "FREEPASS")
    resp = _apply(_client(student.user), slot, voucher_code="FREEPASS")
    app = Application.objects.get(student=student)
    assert resp.status_code == 302 and resp.url == reverse("student-applications")
    assert app.status == ApplicationStatus.PAID
    assert app.payment.is_paid and app.payment.amount == 0 and app.payment.gateway_txn_id == "VOUCHER-FREEPASS"


@pytest.mark.django_db
def test_a_bad_code_stops_before_anything_is_saved(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    resp = _apply(_client(student.user), slot, voucher_code="WRONG123")
    assert resp.status_code == 200 and "voucher_code" in resp.context["form"].errors
    assert not Application.objects.filter(student=student).exists()
    # Without a code the application goes through at the full fee.
    _apply(_client(student.user), slot)
    assert Application.objects.get(student=student).payment.amount == get_application_fee()


@pytest.mark.django_db
def test_the_cancellation_credit_wins_and_the_voucher_is_not_used(places, org):
    student = _student(places, "a@x.tz", reapplication_credit=True)
    slot = _slot(org, places["kinondoni"])
    _voucher()
    _apply(_client(student.user), slot, voucher_code="PROMO2026")
    assert Application.objects.get(student=student).status == ApplicationStatus.PAID
    assert not VoucherRedemption.objects.exists()


@pytest.mark.django_db
def test_rebooking_keeps_the_voucher_while_it_is_still_valid_and_drops_it_when_it_is_not(places, org):
    first, second = _student(places, "a@x.tz"), _student(places, "b@x.tz")
    slot = _slot(org, places["kinondoni"])
    _voucher(max_uses=1)
    c = _client(first.user)
    _apply(c, slot, voucher_code="PROMO2026")
    app = Application.objects.get(student=first)
    Application.objects.filter(pk=app.pk).update(status=ApplicationStatus.UNPAID)  # expired unpaid
    # Another student takes the freed use before the first one comes back.
    _reserve("PROMO2026", second, slot)
    resp = _apply(c, slot)
    app.refresh_from_db()
    assert resp.status_code == 302 and app.status == ApplicationStatus.PENDING
    assert not VoucherRedemption.objects.filter(application=app).exists()
    assert app.payment.amount == get_application_fee()


@pytest.mark.django_db
def test_check_endpoint_prices_a_code_without_reserving_it(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    _voucher()
    c = _client(student.user)
    ok = c.post(reverse("student-voucher-check"), {"code": "promo2026", "slot_id": str(slot.id)}).json()
    assert ok["valid"] and ok["charged"] == "7,500" and ok["discount"] == "7,500"
    bad = c.post(reverse("student-voucher-check"), {"code": "nope", "slot_id": str(slot.id)}).json()
    assert not bad["valid"] and bad["message"] == "This code is not valid."
    assert not VoucherRedemption.objects.exists()
    assert c.get(reverse("student-voucher-check")).status_code == 405


@pytest.mark.django_db
def test_apply_page_shows_the_code_box_but_not_to_credit_holders(places, org):
    slot = _slot(org, places["kinondoni"])
    assert b"data-voucher-box" in _client(_student(places, "a@x.tz").user).get(reverse("student-apply", args=[slot.id])).content
    credit = _student(places, "c@x.tz", reapplication_credit=True)
    assert b"data-voucher-box" not in _client(credit.user).get(reverse("student-apply", args=[slot.id])).content


# ── The code given at registration ──────────────────────────────────────────


def _register(email, code, role="STUDENT"):
    from django.contrib.auth.hashers import make_password

    from apps.accounts.tasks import process_registration_task

    result = process_registration_task(
        {
            "email": email, "password_hash": make_password(PW), "role": role, "first_name": "Neema",
            "last_name": "Said", "phone": "", "voucher_code": code,
        }
    )
    assert result["status"] == "success"
    return User.objects.get(email=email)


@pytest.mark.django_db
def test_registration_saves_a_valid_code_and_tells_the_student():
    _voucher()
    user = _register("new@x.tz", "promo-2026")
    ref = SignupReference.objects.get(user=user)
    assert ref.status == SignupReference.Status.SAVED and ref.voucher.code == "PROMO2026"
    assert Notification.objects.filter(user=user, title="Your offer is saved").exists()


@pytest.mark.django_db
def test_registration_never_fails_because_of_the_code():
    expired = _voucher(_batch(expires_at=timezone.now() - timedelta(days=1)), "OLDCODE")
    user = _register("a@x.tz", "OLDCODE")
    ref = SignupReference.objects.get(user=user)
    assert ref.status == SignupReference.Status.REJECTED and "expired" in ref.reason and ref.voucher == expired
    assert Notification.objects.filter(user=user, title="Your code could not be applied").exists()
    nope = _register("b@x.tz", "doesnotexist")
    assert SignupReference.objects.get(user=nope).status == SignupReference.Status.REJECTED
    # No code, or an organization account: nothing recorded.
    assert not SignupReference.objects.filter(user=_register("c@x.tz", "")).exists()


@pytest.mark.django_db
def test_signup_window_and_named_list_are_checked_at_registration():
    _voucher(_batch(signup_from=timezone.now() + timedelta(days=1)), "EARLY")
    assert SignupReference.objects.get(user=_register("a@x.tz", "EARLY")).status == SignupReference.Status.REJECTED
    _voucher(_batch(allowed_emails=["named@x.tz"]), "NAMED")
    assert SignupReference.objects.get(user=_register("b@x.tz", "NAMED")).status == SignupReference.Status.REJECTED
    assert SignupReference.objects.get(user=_register("named@x.tz", "NAMED")).status == SignupReference.Status.SAVED


@pytest.mark.django_db
def test_the_registration_offer_is_applied_to_the_first_application_only(places, org):
    _voucher(max_uses=10)
    user = _register("new@x.tz", "PROMO2026")
    from apps.students.models import StudentProfile

    student = StudentProfile.objects.create(
        user=user, course="Computer Science", education_level=8, academic_year=3,
        region=places["dar"], district=places["kinondoni"],
    )
    user.phone_verified = True
    user.save(update_fields=["phone_verified"])
    one, two = _slot(org, places["kinondoni"], "One"), _slot(org, places["kinondoni"], "Two")
    c = _client(user)
    page = c.get(reverse("student-apply", args=[one.id]))
    assert page.context["offer_quote"].charged == Decimal("7500")
    _apply(c, one)  # no code typed: the offer from registration applies by itself
    assert Application.objects.get(student=student, slot=one).payment.amount == Decimal("7500")
    assert VoucherRedemption.objects.get(application__slot=one).from_signup
    _apply(c, two)
    assert Application.objects.get(student=student, slot=two).payment.amount == get_application_fee()


@pytest.mark.django_db
def test_a_registration_offer_that_no_longer_works_means_the_full_fee_and_a_notice(places, org):
    voucher = _voucher(max_uses=1)
    user = _register("new@x.tz", "PROMO2026")
    from apps.students.models import StudentProfile

    student = StudentProfile.objects.create(
        user=user, course="Computer Science", education_level=8, academic_year=3,
        region=places["dar"], district=places["kinondoni"],
    )
    user.phone_verified = True
    user.save(update_fields=["phone_verified"])
    slot = _slot(org, places["kinondoni"])
    _reserve("PROMO2026", _student(places, "taker@x.tz"), slot)  # someone else used the only use
    c = _client(user)
    resp = _apply(c, slot)
    assert resp.status_code == 302
    assert Application.objects.get(student=student).payment.amount == get_application_fee()
    assert not VoucherRedemption.objects.filter(student=student).exists()
    assert voucher.batch.name == "Promo"


# ── Database guarantees ─────────────────────────────────────────────────────


def _redemption_row(voucher, student, slot):
    app = create_application(student, slot)
    return VoucherRedemption(
        voucher=voucher, batch=voucher.batch, application=app, student=student,
        fee_amount=FEE, discount_amount=Decimal("7500"), amount_charged=Decimal("7500"),
    )


@pytest.mark.django_db
def test_database_refuses_a_use_beyond_max_uses_even_without_the_service(places, org):
    slot = _slot(org, places["kinondoni"])
    voucher = _voucher(max_uses=1)
    _redemption_row(voucher, _student(places, "a@x.tz"), slot).save()
    with pytest.raises(DatabaseError, match="usage limit"):
        with transaction.atomic():
            _redemption_row(voucher, _student(places, "b@x.tz"), slot).save()


@pytest.mark.django_db
def test_redemptions_are_append_only_and_paid_ones_cannot_be_deleted(places, org):
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    voucher = _voucher()
    row = _redemption_row(voucher, student, slot)
    row.save()
    with pytest.raises(DatabaseError, match="append-only"):
        with transaction.atomic():
            VoucherRedemption.objects.filter(pk=row.pk).update(discount_amount=Decimal("15000"))
    payment = Payment.objects.create(student=student, application=row.application, amount=7500, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id="GW-1")
    with pytest.raises(DatabaseError, match="paid application"):
        with transaction.atomic():
            VoucherRedemption.objects.filter(pk=row.pk).delete()


@pytest.mark.django_db
def test_an_unpaid_redemption_can_be_deleted(places, org):
    row = _redemption_row(_voucher(), _student(places, "a@x.tz"), _slot(org, places["kinondoni"]))
    row.save()
    VoucherRedemption.objects.filter(pk=row.pk).delete()
    assert not VoucherRedemption.objects.exists()


# ── Admin console ───────────────────────────────────────────────────────────


@pytest.fixture
def admin_client(db):
    admin = User.objects.create_user(email="admin@x.tz", password=PW, role=UserRole.SUPER_ADMIN, is_staff=True)
    return _client(admin)


def _batch_post(**over):
    data = {
        "name": "NGO cohort", "description": "Sponsor deal", "discount_type": "PERCENT", "discount_value": "25",
        "per_student_limit": "1", "code_mode": "UNIQUE", "quantity": "5",
    }
    data.update(over)
    return data


@pytest.mark.django_db
def test_only_admins_reach_the_voucher_console(places):
    student = _student(places, "a@x.tz")
    c = _client(student.user)
    for name in ("platform-vouchers", "platform-voucher-new"):
        assert c.get(reverse(name)).status_code == 403
    assert Client().get(reverse("platform-vouchers")).status_code == 302
    assert c.post(reverse("platform-voucher-new"), _batch_post()).status_code == 403
    assert not VoucherBatch.objects.exists()


@pytest.mark.django_db
def test_admin_issues_a_batch_of_unique_codes(admin_client):
    resp = admin_client.post(reverse("platform-voucher-new"), _batch_post())
    batch = VoucherBatch.objects.get()
    assert resp.status_code == 302 and resp.url == reverse("platform-voucher-detail", args=[batch.pk])
    assert batch.codes.count() == 5 and all(c.max_uses == 1 for c in batch.codes.all())
    assert batch.discount_type == DiscountType.PERCENT and batch.discount_value == Decimal("25")
    assert AuditLog.objects.filter(action="Voucher Batch Created", module="Vouchers").exists()
    page = admin_client.get(resp.url)
    assert page.status_code == 200 and batch.codes.first().display_code in page.content.decode()
    assert "NGO cohort" in admin_client.get(reverse("platform-vouchers")).content.decode()


@pytest.mark.django_db
def test_admin_issues_a_shared_campaign_code_with_restrictions(admin_client, places, org):
    udsm = University.objects.get_or_create(name="UDSM")[0]
    data = _batch_post(
        name="Early signup", code_mode="SHARED", shared_code="welcome 2026", shared_max_uses="300",
        universities=[udsm.pk], districts=[places["ilala"].pk], courses="Law\nMedicine", organizations=[org.pk],
        allowed_emails="a@x.tz, b@x.tz", slot_ids=str(_slot(org, places["kinondoni"]).pk),
        slot_start_from="2026-12-01", expires_at="2027-01-31T23:59",
    )
    resp = admin_client.post(reverse("platform-voucher-new"), data)
    assert resp.status_code == 302, getattr(resp, "context", None) and resp.context["form"].errors
    batch = VoucherBatch.objects.get()
    code = batch.codes.get()
    assert code.code == "WELCOME2026" and code.max_uses == 300
    assert list(batch.universities.all()) == [udsm] and list(batch.districts.all()) == [places["ilala"]]
    assert batch.courses == ["Law", "Medicine"] and batch.allowed_emails == ["a@x.tz", "b@x.tz"]
    assert batch.slots.count() == 1 and batch.organizations.count() == 1 and batch.slot_start_from == date(2026, 12, 1)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "over, field",
    [
        ({"discount_type": "PERCENT", "discount_value": "150"}, "discount_value"),
        ({"discount_type": "FIXED", "discount_value": ""}, "discount_value"),
        ({"quantity": ""}, "quantity"),
        ({"starts_at": "2026-12-01T10:00", "expires_at": "2026-11-01T10:00"}, "expires_at"),
        ({"allowed_emails": "not-an-email"}, "allowed_emails"),
        ({"slot_ids": "not-a-uuid"}, "slot_ids"),
        ({"code_mode": "SHARED", "shared_max_uses": ""}, "shared_max_uses"),
    ],
)
def test_batch_form_rejects_bad_terms(admin_client, over, field):
    resp = admin_client.post(reverse("platform-voucher-new"), _batch_post(**over))
    assert resp.status_code == 200 and field in resp.context["form"].errors
    assert not VoucherBatch.objects.exists()


@pytest.mark.django_db
def test_waiver_batch_ignores_the_discount_value(admin_client):
    admin_client.post(reverse("platform-voucher-new"), _batch_post(discount_type="WAIVER", discount_value=""))
    assert VoucherBatch.objects.get().discount_value == 0


@pytest.mark.django_db
def test_admin_pauses_revokes_adds_and_exports(admin_client, places, org):
    admin_client.post(reverse("platform-voucher-new"), _batch_post(quantity="3"))
    batch = VoucherBatch.objects.get()
    url = reverse("platform-voucher-detail", args=[batch.pk])
    student = _student(places, "a@x.tz")
    slot = _slot(org, places["kinondoni"])
    code = batch.codes.first()

    admin_client.post(url, {"action": "toggle"})
    batch.refresh_from_db()
    assert not batch.is_active
    with pytest.raises(services.VoucherError):
        services.quote(code.code, profile=student, slot=slot, fee=FEE)
    admin_client.post(url, {"action": "toggle"})

    admin_client.post(url, {"action": "revoke", "code_id": str(code.pk)})
    code.refresh_from_db()
    assert not code.is_active
    with pytest.raises(services.VoucherError):
        services.quote(code.code, profile=student, slot=slot, fee=FEE)
    admin_client.post(url, {"action": "restore", "code_id": str(code.pk)})
    services.quote(code.code, profile=student, slot=slot, fee=FEE)

    admin_client.post(url, {"action": "add_codes", "count": "4"})
    assert batch.codes.count() == 7

    export = admin_client.get(url, {"export": "csv"})
    assert export["Content-Type"].startswith("text/csv") and export.content.decode().count("\n") == 8
    assert AuditLog.objects.filter(action="Voucher Codes Exported").exists()
    assert {"Voucher Batch Paused", "Voucher Batch Resumed", "Voucher Code Revoked", "Voucher Codes Added"} <= set(
        AuditLog.objects.values_list("action", flat=True)
    )


@pytest.mark.django_db
def test_view_only_admins_cannot_change_or_export(places):
    from apps.accounts.models import AdminRole

    role = AdminRole.objects.create(name="Finance", permissions={"financial_ledger": {"view": True}})
    viewer = User.objects.create_user(email="fin@x.tz", password=PW, role=UserRole.ADMIN, is_staff=True, admin_role=role)
    batch = _batch()
    _voucher(batch)
    c = _client(viewer)
    url = reverse("platform-voucher-detail", args=[batch.pk])
    assert c.get(url).status_code == 200 and c.get(reverse("platform-vouchers")).status_code == 200
    assert c.post(url, {"action": "toggle"}).status_code == 403
    assert c.get(url, {"export": "csv"}).status_code == 403
    assert c.get(reverse("platform-voucher-new")).status_code == 403
    batch.refresh_from_db()
    assert batch.is_active


# ── Ledger ──────────────────────────────────────────────────────────────────


def _pay(student, slot, amount, txn):
    app = create_application(student, slot)
    payment = Payment.objects.create(student=student, application=app, amount=amount, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id=txn)
    return Application.objects.get(pk=app.pk)


@pytest.mark.django_db
def test_ledger_splits_gross_discounts_and_cash(admin_client, places, org):
    slot = _slot(org, places["kinondoni"])
    _pay(_student(places, "cash@x.tz"), slot, 15000, "GW-CASH")  # 15,000 cash
    _pay(_student(places, "credit@x.tz"), slot, 15000, "CREDIT-REAPPLICATION")  # free: not cash
    voucher_student = _student(places, "v@x.tz")
    _voucher()
    app = create_application(voucher_student, slot)
    services.reserve("PROMO2026", profile=voucher_student, slot=slot, application=app, fee=FEE)
    payment = Payment.objects.create(student=voucher_student, application=app, amount=7500, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id="GW-VOUCHER")
    # A reserved-but-unpaid voucher use is not a discount given yet.
    pending = _student(places, "p@x.tz")
    _voucher(_batch(name="Other"), "OTHER1")
    _reserve("OTHER1", pending, slot)

    resp = admin_client.get(reverse("platform-ledger"))
    totals = resp.context["totals"]
    assert totals["cash"] == Decimal("22500")  # 15,000 + 7,500; the credit is not cash
    assert totals["voucher_discounts"] == Decimal("7500") and totals["voucher_count"] == 1
    assert totals["credit_discounts"] == Decimal("15000") and totals["credit_count"] == 1
    assert totals["gross"] == Decimal("45000")
    sources = {p.student.user.email: p.ledger_source for p in resp.context["payments"]}
    assert sources["cash@x.tz"] == "Cash" and sources["credit@x.tz"] == "Credit"
    assert sources["v@x.tz"] == "Voucher PROMO2026"
    voucher_row = next(p for p in resp.context["payments"] if p.student.user.email == "v@x.tz")
    assert (voucher_row.ledger_fee, voucher_row.ledger_discount, voucher_row.ledger_cash) == (FEE, Decimal("7500"), Decimal("7500"))


@pytest.mark.django_db
def test_ledger_source_filter_and_voucher_search(admin_client, places, org):
    slot = _slot(org, places["kinondoni"])
    _pay(_student(places, "cash@x.tz"), slot, 15000, "GW-CASH")
    _pay(_student(places, "credit@x.tz"), slot, 15000, "CREDIT-REAPPLICATION")
    student = _student(places, "v@x.tz")
    _voucher()
    app = create_application(student, slot)
    services.reserve("PROMO2026", profile=student, slot=slot, application=app, fee=FEE)
    finalize_verified_payment(
        Payment.objects.create(student=student, application=app, amount=7500, method=PaymentMethod.M_PESA),
        gateway_txn_id="GW-V",
    )

    def emails(**params):
        resp = admin_client.get(reverse("platform-ledger"), params)
        return {p.student.user.email for p in resp.context["payments"]}

    assert emails(source="voucher") == {"v@x.tz"}
    assert emails(source="credit") == {"credit@x.tz"}
    assert emails(source="cash") == {"cash@x.tz"}
    assert emails(q="promo-2026") == {"v@x.tz"}
    assert len(emails()) == 3


@pytest.mark.django_db
def test_credit_payments_do_not_inflate_the_platform_metrics(admin_client, places, org):
    slot = _slot(org, places["kinondoni"])
    _pay(_student(places, "cash@x.tz"), slot, 15000, "GW-CASH")
    _pay(_student(places, "credit@x.tz"), slot, 15000, "CREDIT-REAPPLICATION")
    from apps.core.views import _compute_metrics

    assert _compute_metrics("30")["total_fees"] == Decimal("15000")
