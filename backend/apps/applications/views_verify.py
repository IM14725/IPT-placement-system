"""Public acceptance-letter verification: what the QR code on a letter opens.

Shows only what is needed to recognise a genuine letter (organization, position, masked student name,
dates, fingerprint) and offers "check a file": upload the PDF you were given and it says whether it is
byte for byte the letter the organization issued. A forged link and an unknown letter give the same answer.
"""
import hashlib

from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from apps.core.rate_limit import token_bucket
from apps.payments.verification import mask_name

from . import letter_services
from .models import AcceptanceLetter

MAX_CHECK_BYTES = 3 * 1024 * 1024  # a little over the 2 MB letter limit; larger files cannot be ours


def _no_store(response):
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _too_many(request, result):
    response = HttpResponse("Too many attempts. Please wait a minute and try again.", status=429, content_type="text/plain")
    response["Retry-After"] = str(int(result.retry_after))
    return _no_store(response)


def _fingerprint(sha256: str) -> str:
    short = sha256[:16].upper()
    return " ".join(short[i:i + 4] for i in range(0, 16, 4))


@require_http_methods(["GET", "POST"])
@token_bucket(capacity=20, refill_per_second=1 / 6, scope="letter-verify", deny_view=_too_many)
def verify_letter(request, letter_id, signature):
    letter = None
    if letter_services.signature_is_valid(letter_id, signature):  # forged links never reach the database
        letter = (
            AcceptanceLetter.objects.select_related(
                "application__student__user", "application__slot__organization", "application__slot__district"
            )
            .filter(id=letter_id)
            .first()
        )
    if letter is None:
        return _no_store(render(request, "applications/verify_letter.html", {"found": False}, status=404))

    application = letter.application
    newest = AcceptanceLetter.objects.filter(application=application).order_by("-version").first()
    context = {
        "found": True,
        "letter": letter,
        "reference": letter_services.letter_reference(letter.id),
        "organization": application.slot.organization.name,
        "position": application.slot.title,
        "location": str(application.slot.district),
        "student": mask_name(application.student.user.full_name or application.student.user.email),
        "issued": letter.created_at,
        "superseded": not letter.is_current,
        "replaced_on": newest.created_at if newest and newest.pk != letter.pk else None,
        "fingerprint": _fingerprint(letter.sha256),
    }
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None or upload.size > MAX_CHECK_BYTES:
            context["check"] = "invalid"
        else:
            digest = hashlib.sha256(upload.read()).hexdigest()
            match = AcceptanceLetter.objects.filter(application=application, sha256=digest).first()
            if match is None:
                context["check"] = "different"
            elif match.pk != letter.pk:
                context["check"] = "other_version"
            else:
                context["check"] = "match"
    return _no_store(render(request, "applications/verify_letter.html", context))
