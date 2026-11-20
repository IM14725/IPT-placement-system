"""PAYMENT_WAIVED: applications go through without a fee while there is no payment key."""
import pytest
from django.urls import reverse

from apps.applications.models import Application, ApplicationStatus
from apps.core.views import ledger  # noqa: F401  (imported so a broken ledger view fails here too)
from apps.payments.models import Payment
from apps.students.test_dashboard_features import _student
from apps.vouchers import stats
from apps.vouchers.models import DiscountType, VoucherRedemption
from apps.vouchers.tests import _apply, _batch, _client, _slot, _voucher, org, places  # noqa: F401


@pytest.fixture
def waived(settings):
    settings.PAYMENT_WAIVED = True


@pytest.mark.django_db
def test_waived_application_is_paid_at_once_for_zero_and_is_not_cash(places, org, waived):
    student = _student(places, "w@x.tz")
    slot = _slot(org, places["kinondoni"])
    resp = _apply(_client(student.user), slot)
    app = Application.objects.get(student=student)
    assert resp.status_code == 302 and resp.url == reverse("student-applications")
    assert app.status == ApplicationStatus.PAID and app.can_cancel is False
    assert app.payment.is_paid and app.payment.amount == 0
    assert app.payment.gateway_txn_id.startswith("WAIVED-")
    totals = stats.ledger_totals()
    assert totals["cash"] == 0 and totals["waived_count"] == 1 and totals["paid_count"] == 0
    assert stats.describe_payment(app.payment).ledger_source == "Waived"


@pytest.mark.django_db
def test_waived_apply_leaves_credit_and_vouchers_untouched(places, org, waived):
    student = _student(places, "w@x.tz")
    student.reapplication_credit = True
    student.save(update_fields=["reapplication_credit"])
    voucher = _voucher(_batch(discount_type=DiscountType.WAIVER), "FREEPASS")
    _apply(_client(student.user), _slot(org, places["kinondoni"]), voucher_code="FREEPASS")
    student.refresh_from_db()
    assert student.reapplication_credit is True
    assert not VoucherRedemption.objects.filter(voucher=voucher).exists()


@pytest.mark.django_db
def test_a_seat_held_before_the_waiver_is_finalized_by_applying_again(places, org, settings):
    student = _student(places, "w@x.tz")
    slot = _slot(org, places["kinondoni"])
    client = _client(student.user)
    _apply(client, slot)  # no waiver yet: PENDING, fee owed
    assert Application.objects.get(student=student).status == ApplicationStatus.PENDING
    settings.PAYMENT_WAIVED = True
    _apply(client, slot)
    app = Application.objects.get(student=student)
    assert app.status == ApplicationStatus.PAID and Payment.objects.filter(application=app).count() == 1
    assert app.payment.amount == 0


@pytest.mark.django_db
def test_waiver_off_keeps_the_normal_fee_flow(places, org):
    student = _student(places, "w@x.tz")
    _apply(_client(student.user), _slot(org, places["kinondoni"]))
    app = Application.objects.get(student=student)
    assert app.status == ApplicationStatus.PENDING and app.payment.amount > 0


@pytest.mark.django_db
def test_apply_page_says_no_fee_when_waived(places, org, waived):
    student = _student(places, "w@x.tz")
    slot = _slot(org, places["kinondoni"])
    page = _client(student.user).get(reverse("student-apply", args=[slot.id])).content
    assert b"No application fee is charged right now" in page and b"data-voucher-box" not in page
