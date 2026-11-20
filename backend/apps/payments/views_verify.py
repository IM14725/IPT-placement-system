"""Public receipt verification: what the QR code on a receipt opens.

Anyone holding the link can see it, so it shows only what is needed to recognise a genuine receipt: the
reference, date, amount, organization and placement, and the student's name masked ("A*** J***"). No
email, phone or ids. A wrong or forged link and an unknown reference give the same answer, so references
cannot be probed.
"""
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.core.rate_limit import token_bucket

from .models import Payment, PaymentStatus
from .services import receipt_summary
from .verification import mask_name, signature_is_valid


def _no_store(response):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _too_many(request, result):
    response = HttpResponse("Too many attempts. Please wait a minute and try again.", status=429, content_type="text/plain")
    response["Retry-After"] = str(int(result.retry_after))
    return _no_store(response)


@require_GET
@token_bucket(capacity=20, refill_per_second=1 / 6, scope="receipt-verify", deny_view=_too_many)
def verify_receipt(request, reference, signature):
    payment = None
    if signature_is_valid(reference, signature):  # forged links never reach the database
        payment = (
            Payment.objects.select_related(
                "student__user", "application__slot__organization", "application__slot__district"
            )
            .filter(reference_id=reference, status=PaymentStatus.PAID)
            .first()
        )
    if payment is None:
        return _no_store(render(request, "payments/verify_receipt.html", {"found": False}, status=404))

    data = receipt_summary(payment)
    data["student"] = mask_name(data["student"])
    context = {
        "found": True,
        "r": data,
        "cancelled": payment.application.status == "CANCELLED",
    }
    return _no_store(render(request, "payments/verify_receipt.html", context))
