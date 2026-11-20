"""Professional receipt: HTML email with a QR code, redesigned PDF, and the public page the QR opens."""
import re
from decimal import Decimal

import pytest
from django.core import mail
from django.test import Client
from django.urls import reverse

from apps.applications.models import Application
from apps.applications.services import finalize_verified_payment
from apps.payments import verification
from apps.payments.models import Payment
from apps.payments.receipt_email import build_receipt_email
from apps.payments.services import build_payment_receipt, receipt_summary
from apps.payments.utils import WAIVED_TXN_ID
from apps.students.test_dashboard_features import _student
from apps.vouchers.tests import _slot, org, places  # noqa: F401


def _paid(places, org, *, txn="SEL-123", amount=Decimal("15000.00"), email="r@x.tz", payload=None):
    student = _student(places, email)
    slot = _slot(org, places["kinondoni"])
    app = Application.objects.create(student=student, slot=slot)
    payment = Payment.objects.create(student=student, application=app, amount=amount, method="M_PESA")
    finalize_verified_payment(payment, gateway_txn_id=txn, payload=payload or {})
    payment.refresh_from_db()
    return payment


def test_signature_only_matches_its_own_reference():
    sig = verification._signature("IPT-AAAAAAAAAA")
    assert verification.signature_is_valid("IPT-AAAAAAAAAA", sig)
    assert not verification.signature_is_valid("IPT-BBBBBBBBBB", sig)
    assert not verification.signature_is_valid("IPT-AAAAAAAAAA", "") and not verification.signature_is_valid("", sig)


def test_names_are_masked():
    assert verification.mask_name("Asha Juma") == "A*** J***"
    assert verification.mask_name("") == "-"


def test_qr_png_is_a_png():
    assert verification.qr_png("https://example.tz/verify/x/y/").startswith(b"\x89PNG")


@pytest.mark.django_db
def test_verification_page_shows_masked_details_for_a_genuine_receipt(places, org):
    payment = _paid(places, org)
    page = Client().get(verification.verification_path(payment))
    html = page.content.decode()
    assert page.status_code == 200 and "data-receipt-genuine" in html
    assert payment.reference_id in html and "Alpha Ltd" in html and "15,000.00" in html
    assert payment.student.user.email not in html and "@" not in html.split("<main")[-1].split("</main>")[0]
    assert page["X-Robots-Tag"] == "noindex, nofollow" and "no-store" in page["Cache-Control"]


@pytest.mark.django_db
def test_forged_and_unknown_links_give_the_same_not_verified_answer(places, org):
    payment = _paid(places, org)
    forged = Client().get(reverse("receipt-verify", args=[payment.reference_id, "0" * 24]))
    unknown_ref = "IPT-ZZZZZZZZZZ"
    unknown = Client().get(reverse("receipt-verify", args=[unknown_ref, verification._signature(unknown_ref)]))
    for resp in (forged, unknown):
        assert resp.status_code == 404 and b"data-receipt-unverified" in resp.content and b"data-receipt-genuine" not in resp.content


@pytest.mark.django_db
def test_unpaid_payment_cannot_be_verified(places, org):
    student = _student(places, "u@x.tz")
    app = Application.objects.create(student=student, slot=_slot(org, places["kinondoni"]))
    payment = Payment.objects.create(student=student, application=app, amount=Decimal("15000"))
    assert Client().get(verification.verification_path(payment)).status_code == 404


@pytest.mark.django_db
def test_waived_receipt_says_no_fee_and_is_marked(places, org):
    payment = _paid(places, org, txn=WAIVED_TXN_ID, amount=Decimal("0"), payload={"waived": True, "fee": "15000.00"})
    r = receipt_summary(payment)
    assert r["badge"] == "FEE WAIVED" and r["paid"] == "TZS 0.00" and r["fee"] == "TZS 15,000.00" and r["discount"]
    assert b"FEE WAIVED" in Client().get(verification.verification_path(payment)).content


@pytest.mark.django_db
def test_receipt_email_has_the_card_the_qr_and_the_verify_link(places, org, settings):
    settings.EMAIL_LOGO_URL = ""  # a developer's .env may host the logo; this test checks the embedded one
    payment = _paid(places, org)
    html, images = build_receipt_email(payment, "Payment Receipt")
    assert payment.reference_id in html and "TZS 15,000.00" in html and "Total paid" in html
    assert f"cid:{verification.QR_CID}" in html and "cid:ipt-logo" in html
    assert verification.verification_url(payment) in html
    assert images[verification.QR_CID].startswith(b"\x89PNG")


@pytest.mark.django_db
def test_receipt_task_sends_html_with_both_images_inline_and_the_pdf_attached(places, org, settings):
    settings.EMAIL_LOGO_URL = ""
    from apps.payments.tasks import receipt_email

    mail.outbox.clear()
    payment = _paid(places, org)
    receipt_email(payment.id)  # eager Celery already ran it once on finalize; run explicitly for the assertions
    sent = mail.outbox[-1]
    assert verification.verification_url(payment) in sent.body
    raw = sent.message()
    related = raw.get_body(preferencelist=("related",)) or raw.get_payload()[-1]
    cids = re.findall(r"Content-ID: <([^>]+)>", related.as_string())
    assert "ipt-logo" in cids and verification.QR_CID in cids
    assert any(name.endswith(".pdf") for name, *_ in sent.attachments)


@pytest.mark.django_db
def test_pdf_receipt_is_built_with_qr_and_link(places, org, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    payment = _paid(places, org)
    payment.receipt_pdf = None
    payment.save(update_fields=["receipt_pdf"])
    path = build_payment_receipt(payment)
    data = open(path, "rb").read()
    assert data.startswith(b"%PDF") and len(data) > 5000
    assert verification.verification_url(payment).encode() in data  # the clickable link annotation


@pytest.mark.django_db
def test_qr_and_logo_are_embedded_even_when_a_hosted_logo_url_is_configured(places, org, settings):
    """Regression: with EMAIL_LOGO_URL set the QR image used to be dropped, leaving an empty placeholder."""
    from apps.core import providers

    settings.EMAIL_LOGO_URL = "https://cdn.example.tz/logo.png"
    payment = _paid(places, org)
    html, images = build_receipt_email(payment, "Payment Receipt")
    assert "cid:ipt-logo" in html and "cdn.example.tz" not in html
    mail.outbox.clear()
    providers.send_email("Receipt", ["a@x.tz"], "text", attachments=[{"filename": "r.pdf", "content": b"%PDF", "mimetype": "application/pdf"}],
                         html_content=html, inline_images=images)
    raw = mail.outbox[-1].message().as_string()
    assert "Content-ID: <ipt-logo>" in raw and f"Content-ID: <{verification.QR_CID}>" in raw
    assert raw.count("Content-Disposition: inline") >= 2
