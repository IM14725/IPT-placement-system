"""Voucher rules: validating a code, pricing it, reserving a use, and the signup reference.

Every rule that decides "may this student use this code on this slot" lives in
``voucher_blocker`` (the same shape as ``applications.services.eligibility_blocker``): it returns a
message that is safe to show the student, or ``""`` when the code can be used.

A use is *reserved* (``reserve``) when the application is created, inside the same transaction as
the Payment, with row locks on the code, the batch and the student. It is released simply by the
application leaving PENDING/PAID (see ``models`` docstring), so nothing here has to run on expiry.
"""

import re
import secrets
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext

from apps.vouchers.models import (
    ACTIVE_APPLICATION_STATUSES,
    DiscountType,
    FeeVoucher,
    SignupReference,
    VoucherBatch,
    VoucherRedemption,
)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: codes get read out and typed
CODE_PREFIX = "IPT"
CODE_RANDOM_LENGTH = 8
MAX_CODES_PER_REQUEST = 5000


class VoucherError(Exception):
    """The code cannot be used (the message is safe to show the student)."""


def normalize_code(raw) -> str:
    """Upper case letters and digits only, so ``ipt-7k3f 9qxm`` finds ``IPT7K3F9QXM``."""
    return re.sub(r"[^A-Z0-9]", "", str(raw or "").upper())[:40]


def generate_code() -> str:
    return CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_RANDOM_LENGTH))


@transaction.atomic
def create_codes(batch, count, *, max_uses=1) -> list:
    """Create ``count`` unique generated codes for ``batch`` (retrying on the rare collision)."""
    count = max(0, min(int(count), MAX_CODES_PER_REQUEST))
    created = []
    attempts = 0
    while len(created) < count and attempts < 20:
        attempts += 1
        wanted = {generate_code() for _ in range(count - len(created))}
        taken = set(FeeVoucher.objects.filter(code__in=wanted).values_list("code", flat=True))
        rows = [FeeVoucher(batch=batch, code=c, max_uses=max_uses) for c in sorted(wanted - taken)]
        FeeVoucher.objects.bulk_create(rows)
        created.extend(rows)
    return created


# ── Pricing ──────────────────────────────────────────────────────────────────


def compute_discount(batch, fee):
    """``(discount, amount_to_pay)`` for ``fee`` under the batch's terms, in whole shillings."""
    fee = Decimal(fee)
    if batch.discount_type == DiscountType.WAIVER:
        discount = fee
    elif batch.discount_type == DiscountType.PERCENT:
        discount = (fee * Decimal(batch.discount_value) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        discount = Decimal(batch.discount_value)
    discount = max(Decimal("0"), min(discount, fee))
    return discount, fee - discount


# ── Usage counting (a use is held only while its application is PENDING or PAID) ──


def _active_redemptions(**filters):
    return VoucherRedemption.objects.filter(application__status__in=ACTIVE_APPLICATION_STATUSES, **filters)


def voucher_uses(voucher, exclude_application=None) -> int:
    qs = _active_redemptions(voucher=voucher)
    if exclude_application is not None:
        qs = qs.exclude(application=exclude_application)
    return qs.count()


def batch_discount_given(batch, exclude_application=None) -> Decimal:
    qs = _active_redemptions(batch=batch)
    if exclude_application is not None:
        qs = qs.exclude(application=exclude_application)
    return qs.aggregate(total=Sum("discount_amount"))["total"] or Decimal("0")


def student_uses(batch, profile, exclude_application=None) -> int:
    qs = _active_redemptions(batch=batch, student=profile)
    if exclude_application is not None:
        qs = qs.exclude(application=exclude_application)
    return qs.count()


def student_has_waiver(profile, exclude_application=None) -> bool:
    qs = _active_redemptions(student=profile, amount_charged=0)
    if exclude_application is not None:
        qs = qs.exclude(application=exclude_application)
    return qs.exists()


# ── Rules ────────────────────────────────────────────────────────────────────


def find_voucher(raw, *, lock=False):
    code = normalize_code(raw)
    if not code:
        return None
    qs = FeeVoucher.objects.select_related("batch")
    if lock:
        qs = qs.select_for_update(of=("self",))
    return qs.filter(code=code).first()


def _ids(batch, relation):
    return set(getattr(batch, relation).values_list("pk", flat=True))


def student_restriction_blocker(batch, user, profile) -> str:
    """Restrictions about the student. Profile-based ones are skipped while there is no profile yet
    (right after registration); they are checked in full when the student applies."""
    joined = getattr(user, "created_at", None) or user.date_joined
    if batch.signup_from and joined < batch.signup_from:
        return gettext("This code is only for students who registered during a specific period.")
    if batch.signup_to and joined > batch.signup_to:
        return gettext("This code is only for students who registered during a specific period.")
    if batch.allowed_emails and (user.email or "").strip().lower() not in {
        str(e).strip().lower() for e in batch.allowed_emails
    }:
        return gettext("This code was issued to specific students and your account is not on the list.")
    if profile is None:
        return ""
    universities = _ids(batch, "universities")
    if universities and profile.university_id not in universities:
        return gettext("This code is only for students of specific universities.")
    courses = {str(c).strip().casefold() for c in batch.courses if str(c).strip()}
    if courses and (profile.course or "").strip().casefold() not in courses:
        return gettext("This code is only for students of specific courses.")
    regions = _ids(batch, "regions")
    if regions and profile.region_id not in regions:
        return gettext("This code is only for students from specific regions.")
    districts = _ids(batch, "districts")
    if districts and profile.district_id not in districts:
        return gettext("This code is only for students from specific districts.")
    return ""


def slot_restriction_blocker(batch, slot) -> str:
    message = gettext("This code does not apply to this slot.")
    organizations = _ids(batch, "organizations")
    if organizations and slot.organization_id not in organizations:
        return message
    slots = _ids(batch, "slots")
    if slots and slot.pk not in slots:
        return message
    districts = _ids(batch, "slot_districts")
    if districts and slot.district_id not in districts:
        return message
    if batch.slot_start_from or batch.slot_start_to:
        start = slot.start_date
        if start is None:
            return message
        if batch.slot_start_from and start < batch.slot_start_from:
            return message
        if batch.slot_start_to and start > batch.slot_start_to:
            return message
    return ""


def voucher_blocker(voucher, *, user, profile=None, slot=None, exclude_application=None, now=None) -> str:
    """Why this voucher cannot be used now, or ``""`` when it can.

    ``profile``/``slot`` may be ``None`` (right after registration, before either exists): only the
    rules that can already be decided are checked.
    """
    now = now or timezone.now()
    if voucher is None or not voucher.is_active or not voucher.batch.is_active:
        return gettext("This code is not valid.")
    batch = voucher.batch
    if batch.starts_at and now < batch.starts_at:
        return gettext("This code is not active yet.")
    if batch.expires_at and now > batch.expires_at:
        return gettext("This code has expired.")
    if voucher_uses(voucher, exclude_application) >= voucher.max_uses:
        return gettext("This code has already been used the maximum number of times.")
    if batch.budget_tzs is not None and batch_discount_given(batch, exclude_application) >= batch.budget_tzs:
        return gettext("This offer has run out of funds.")
    blocker = student_restriction_blocker(batch, user, profile)
    if blocker:
        return blocker
    if profile is not None and student_uses(batch, profile, exclude_application) >= batch.per_student_limit:
        return gettext("You have already used this offer.")
    if slot is not None:
        return slot_restriction_blocker(batch, slot)
    return ""


# ── Checking and reserving ───────────────────────────────────────────────────


@dataclass
class Quote:
    voucher: FeeVoucher
    fee: Decimal
    discount: Decimal
    charged: Decimal


def _price_blocker(batch, profile, fee, exclude_application):
    """Rules that depend on the price: the budget must cover this discount and a student gets one waiver."""
    discount, charged = compute_discount(batch, fee)
    if batch.budget_tzs is not None and batch_discount_given(batch, exclude_application) + discount > batch.budget_tzs:
        return gettext("This offer has run out of funds."), discount, charged
    if charged == 0 and student_has_waiver(profile, exclude_application):
        return gettext("You have already used a full waiver. Only one is allowed per student."), discount, charged
    return "", discount, charged


def quote(raw_code, *, profile, slot, fee, exclude_application=None) -> Quote:
    """Price ``raw_code`` for this student and slot without reserving anything. Raises ``VoucherError``."""
    voucher = find_voucher(raw_code)
    if voucher is None:
        raise VoucherError(gettext("This code is not valid."))
    blocker = voucher_blocker(
        voucher, user=profile.user, profile=profile, slot=slot, exclude_application=exclude_application
    )
    if blocker:
        raise VoucherError(blocker)
    blocker, discount, charged = _price_blocker(voucher.batch, profile, fee, exclude_application)
    if blocker:
        raise VoucherError(blocker)
    return Quote(voucher=voucher, fee=Decimal(fee), discount=discount, charged=charged)


@transaction.atomic
def reserve(raw_code, *, profile, slot, application, fee, from_signup=False, request=None) -> VoucherRedemption:
    """Hold one use of the code for ``application`` (call inside the application's transaction).

    Locks the code, its batch and the student's profile, so two students cannot take the last use,
    overspend a budget, or one student cannot claim two waivers by applying twice at once. Replaces
    any redemption the application already had (a re-booked seat). Raises ``VoucherError``.
    """
    from apps.core.audit import audit_event
    from apps.students.models import StudentProfile

    voucher = find_voucher(raw_code, lock=True)
    if voucher is None:
        raise VoucherError(gettext("This code is not valid."))
    batch = VoucherBatch.objects.select_for_update().get(pk=voucher.batch_id)
    voucher.batch = batch
    StudentProfile.objects.select_for_update().filter(pk=profile.pk).first()

    blocker = voucher_blocker(
        voucher, user=profile.user, profile=profile, slot=slot, exclude_application=application
    )
    if not blocker:
        blocker, discount, charged = _price_blocker(batch, profile, fee, application)
    if blocker:
        raise VoucherError(blocker)

    VoucherRedemption.objects.filter(application=application).delete()
    redemption = VoucherRedemption.objects.create(
        voucher=voucher,
        batch=batch,
        application=application,
        student=profile,
        fee_amount=Decimal(fee),
        discount_amount=discount,
        amount_charged=charged,
        from_signup=from_signup,
    )
    audit_event(
        request,
        action="Voucher Applied",
        module="Vouchers",
        actor=profile.user,
        target=redemption,
        target_label=f"{voucher.display_code} -> {slot.title}",
        description=(
            f"Voucher {voucher.display_code} ({batch.name}) applied: fee {fee}, discount {discount}, "
            f"to pay {charged}" + (" (from registration)" if from_signup else "")
        ),
        context={"batch_id": str(batch.pk), "application_id": str(application.pk)},
    )
    return redemption


@dataclass
class CheckoutOffer:
    redemption: VoucherRedemption | None = None
    notice: str = ""

    @property
    def amount(self):
        return self.redemption.amount_charged if self.redemption else None


def signup_voucher_for(profile):
    """The voucher saved from registration, if this student can still receive it (else ``None``)."""
    ref = SignupReference.objects.filter(user=profile.user, status=SignupReference.Status.SAVED).select_related(
        "voucher__batch"
    ).first()
    if ref is None or ref.voucher is None:
        return None
    # "First application": once the offer holds a use for this student it is not applied again.
    if student_uses(ref.voucher.batch, profile) >= ref.voucher.batch.per_student_limit:
        return None
    return ref.voucher


def resolve_checkout_offer(*, profile, slot, application, fee, typed_code="", request=None) -> CheckoutOffer:
    """Decide which voucher (if any) prices this application, and reserve its use.

    A code the student typed wins and raises ``VoucherError`` when it is not usable. A voucher the
    application already holds (a re-booked seat) is re-checked. Otherwise the code from
    registration is applied automatically; when that one no longer works the student simply pays
    the full fee and is told why.
    """
    typed_code = normalize_code(typed_code)
    if typed_code:
        return CheckoutOffer(
            reserve(typed_code, profile=profile, slot=slot, application=application, fee=fee, request=request)
        )

    existing = VoucherRedemption.objects.filter(application=application).select_related("voucher").first()
    if existing is not None:
        try:
            redemption = reserve(
                existing.voucher.code, profile=profile, slot=slot, application=application, fee=fee,
                from_signup=existing.from_signup, request=request,
            )
            return CheckoutOffer(redemption)
        except VoucherError as exc:
            existing.delete()
            return CheckoutOffer(
                notice=gettext("Your voucher no longer applies (%(reason)s) so the full fee is charged.")
                % {"reason": str(exc).rstrip(".")}
            )

    voucher = signup_voucher_for(profile)
    if voucher is not None:
        try:
            return CheckoutOffer(
                reserve(
                    voucher.code, profile=profile, slot=slot, application=application, fee=fee,
                    from_signup=True, request=request,
                )
            )
        except VoucherError as exc:
            return CheckoutOffer(
                notice=gettext("Your registration offer was not applied: %(reason)s") % {"reason": str(exc)}
            )
    return CheckoutOffer()


def preview_checkout_offer(*, profile, slot, fee, exclude_application=None):
    """What the apply page shows before the student commits: ``(quote or None, notice)``. Writes nothing."""
    voucher = signup_voucher_for(profile)
    if voucher is None:
        return None, ""
    try:
        return quote(voucher.code, profile=profile, slot=slot, fee=fee, exclude_application=exclude_application), ""
    except VoucherError as exc:
        return None, gettext("Your registration offer cannot be used for this application: %(reason)s") % {
            "reason": str(exc)
        }


# ── Registration reference ───────────────────────────────────────────────────


def attach_signup_reference(user, raw_code):
    """Record the code a student gave at registration and tell them what happened.

    Never raises into registration: a bad code only produces a notice.
    """
    from apps.notifications.inapp import NotificationType, notify

    code = normalize_code(raw_code)
    if not code or SignupReference.objects.filter(user=user).exists():
        return None
    voucher = find_voucher(code)
    if voucher is None:
        reason = gettext("This code is not valid.")
    else:
        reason = voucher_blocker(voucher, user=user)
    status = SignupReference.Status.REJECTED if reason else SignupReference.Status.SAVED
    ref = SignupReference.objects.create(
        user=user,
        raw_code=str(raw_code or "").strip()[:60],
        voucher=voucher,
        status=status,
        reason=str(reason)[:255],
    )
    if status == SignupReference.Status.SAVED:
        notify(
            user,
            NotificationType.PAYMENT,
            gettext("Your offer is saved"),
            gettext(
                "The code you entered when registering is valid (%(batch)s: %(terms)s). It is applied "
                "automatically to your first application."
            ) % {"batch": voucher.batch.name, "terms": voucher.batch.discount_label},
            link="/student/marketplace/",
        )
    else:
        notify(
            user,
            NotificationType.PAYMENT,
            gettext("Your code could not be applied"),
            gettext(
                "The code you entered when registering was not applied: %(reason)s Your account is "
                "ready; you can enter another code when you apply."
            ) % {"reason": reason},
            link="/student/marketplace/",
        )
    return ref
