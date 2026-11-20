"""Numbers for the admin console and the platform ledger.

"Given" means the application is PAID: a discount on a seat that is only reserved is not money
given away yet, and one that expires unpaid never is. "Holding" counts PENDING and PAID, the uses
that stop a code from being used again.
"""

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q, Sum

from apps.payments.models import Payment, PaymentStatus
from apps.payments.utils import CREDIT_TXN_PREFIX, WAIVED_TXN_PREFIX
from apps.vouchers.models import ACTIVE_APPLICATION_STATUSES, FeeVoucher, SignupReference, VoucherRedemption

ZERO = Decimal("0")


def batch_stats(batch_ids):
    """``{batch_id: {...}}`` for the given batches, in a handful of queries."""
    ids = list(batch_ids)
    stats = {
        i: {
            "codes": 0, "revoked": 0, "holding": 0, "paid": 0, "given": ZERO, "collected": ZERO,
            "signup_saved": 0, "signup_converted": 0,
        }
        for i in ids
    }
    for row in FeeVoucher.objects.filter(batch_id__in=ids).values("batch_id").annotate(
        n=Count("id"), revoked=Count("id", filter=Q(is_active=False))
    ):
        stats[row["batch_id"]]["codes"] = row["n"]
        stats[row["batch_id"]]["revoked"] = row["revoked"]
    for row in (
        VoucherRedemption.objects.filter(batch_id__in=ids)
        .values("batch_id")
        .annotate(
            holding=Count("id", filter=Q(application__status__in=ACTIVE_APPLICATION_STATUSES)),
            paid=Count("id", filter=Q(application__status="PAID")),
            given=Sum("discount_amount", filter=Q(application__status="PAID")),
            collected=Sum("amount_charged", filter=Q(application__status="PAID")),
            converted=Count("student", filter=Q(application__status="PAID", from_signup=True), distinct=True),
        )
    ):
        entry = stats[row["batch_id"]]
        entry.update(
            holding=row["holding"], paid=row["paid"], given=row["given"] or ZERO,
            collected=row["collected"] or ZERO, signup_converted=row["converted"],
        )
    for row in (
        SignupReference.objects.filter(voucher__batch_id__in=ids, status=SignupReference.Status.SAVED)
        .values("voucher__batch_id")
        .annotate(n=Count("id"))
    ):
        stats[row["voucher__batch_id"]]["signup_saved"] = row["n"]
    return stats


def signup_overview():
    """Registration references: how many were saved and how many were lost to a bad code."""
    return {
        "saved": SignupReference.objects.filter(status=SignupReference.Status.SAVED).count(),
        "rejected": SignupReference.objects.filter(status=SignupReference.Status.REJECTED).count(),
    }


def ledger_totals():
    """Gross fees, discounts and cash for the ledger header.

    Cash collected is what mobile money actually received. A cancellation credit is a free
    application recorded at the full fee, so it counts as a discount, not as cash.
    """
    paid = Payment.objects.filter(status=PaymentStatus.PAID)
    credit = paid.filter(gateway_txn_id__startswith=CREDIT_TXN_PREFIX).aggregate(total=Sum("amount"), n=Count("id"))
    waived_n = paid.filter(gateway_txn_id__startswith=WAIVED_TXN_PREFIX).count()
    cash = (
        paid.exclude(gateway_txn_id__startswith=CREDIT_TXN_PREFIX)
        .exclude(gateway_txn_id__startswith=WAIVED_TXN_PREFIX)
        .aggregate(total=Sum("amount"), n=Count("id"))
    )
    vouchers = VoucherRedemption.objects.filter(application__status="PAID").aggregate(
        total=Sum("discount_amount"), n=Count("id")
    )
    cash_total = cash["total"] or ZERO
    voucher_total = vouchers["total"] or ZERO
    credit_total = credit["total"] or ZERO
    return {
        "cash": cash_total,
        "voucher_discounts": voucher_total,
        "credit_discounts": credit_total,
        "gross": cash_total + voucher_total + credit_total,
        "paid_count": (cash["n"] or 0) + (credit["n"] or 0),
        "voucher_count": vouchers["n"] or 0,
        "credit_count": credit["n"] or 0,
        # Applications let through while no payment key was set: recorded at 0, so no money either way.
        "waived_count": waived_n,
    }


def describe_payment(payment):
    """Fee, discount, cash and source of one ledger row (adds ``ledger_*`` attributes)."""
    redemption = _redemption(payment)
    if (payment.gateway_txn_id or "").startswith(WAIVED_TXN_PREFIX):
        fee, discount, cash, source = ZERO, ZERO, ZERO, "Waived"
    elif (payment.gateway_txn_id or "").startswith(CREDIT_TXN_PREFIX):
        fee, discount, cash, source = payment.amount, payment.amount, ZERO, "Credit"
    elif redemption is not None:
        fee, discount, cash = redemption.fee_amount, redemption.discount_amount, payment.amount
        source = f"Voucher {redemption.voucher.display_code}"
    else:
        fee, discount, cash, source = payment.amount, ZERO, payment.amount, "Cash"
    payment.ledger_fee, payment.ledger_discount, payment.ledger_cash, payment.ledger_source = fee, discount, cash, source
    return payment


def _redemption(payment):
    try:
        return payment.application.voucher_redemption
    except ObjectDoesNotExist:  # a normal payment with no voucher
        return None
