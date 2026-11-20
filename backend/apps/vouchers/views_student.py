from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.translation import gettext
from django.views.decorators.http import require_POST

from apps.core.rate_limit import token_bucket
from apps.payments.utils import get_application_fee
from apps.vouchers import services


@login_required
@require_POST
@token_bucket(capacity=8, refill_per_second=1 / 20, scope="voucher-check", fail_closed=True)
def check_code(request):
    """Price a typed voucher code for the apply page. Writes nothing.

    Strictly rate limited and always answers with the same generic wording for an unknown code, so
    it cannot be used to guess codes.
    """
    from apps.applications.models import Application
    from apps.slots.models import Slot
    from apps.students.models import StudentProfile

    if not request.user.is_student:
        return JsonResponse({"valid": False, "message": gettext("Student account required.")}, status=403)
    profile = StudentProfile.objects.filter(user=request.user).first()
    slot_id = request.POST.get("slot_id")
    slot = Slot.objects.filter(pk=slot_id).first() if _is_uuid(slot_id) else None
    if profile is None or slot is None:
        return JsonResponse({"valid": False, "message": gettext("This code is not valid.")})
    held = Application.objects.filter(student=profile, slot=slot).first()
    try:
        quote = services.quote(
            request.POST.get("code"), profile=profile, slot=slot, fee=get_application_fee(), exclude_application=held
        )
    except services.VoucherError as exc:
        return JsonResponse({"valid": False, "message": str(exc)})
    label = quote.voucher.batch.discount_label
    figures = {"label": label, "fee": f"{quote.fee:,.0f}", "discount": f"{quote.discount:,.0f}", "charged": f"{quote.charged:,.0f}"}
    summary = (
        gettext("%(label)s: the fee is fully covered. You pay TZS 0.") % figures
        if quote.charged == 0
        else gettext("%(label)s: fee TZS %(fee)s, discount TZS %(discount)s. You pay TZS %(charged)s.") % figures
    )
    return JsonResponse(
        {
            "valid": True,
            "label": label,
            "fee": figures["fee"],
            "discount": figures["discount"],
            "charged": figures["charged"],
            "free": quote.charged == 0,
            "summary": summary,
        }
    )


def _is_uuid(value):
    import uuid

    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError):
        return False
    return True
