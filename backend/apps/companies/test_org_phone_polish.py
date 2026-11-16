"""Phone fixes for the organization slot and applicant pages (review of 2026-11-16)."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document
from apps.documents.tests import make_png_bytes
from apps.students.test_dashboard_features import (  # noqa: F401
    _client,
    _pay,
    _reset_redis_state,
    _slot,
    _student,
    org,
    places,
)


@pytest.fixture
def ready_org(org):
    """The shared test organization with the three required documents, so no page redirects to the profile."""
    for doc_type in (Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE):
        Document.objects.create(
            owner=org.user, doc_type=doc_type, original_name="c.png",
            file=SimpleUploadedFile("c.png", make_png_bytes(), content_type="image/png"),
        )
    return org


def _applicants_page(ready_org, places, slot, n=1):
    for i in range(n):
        _pay(_student(places, email=f"s{i}@x.tz"), slot)
    return _client(ready_org.user).get(reverse("organization-applicants", args=[slot.id])).content.decode()


# ── 1. "80% Filled", not "80%% Filled" ──────────────────────────────────────────

@pytest.mark.django_db
def test_slot_applicants_capacity_reads_one_percent_sign(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=5)
    body = _applicants_page(ready_org, places, slot, n=4)
    assert "80% Filled" in body and "%%" not in body


@pytest.mark.django_db
def test_slot_applicants_capacity_percent_in_kiswahili(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=5)
    for i in range(4):
        _pay(_student(places, email=f"s{i}@x.tz"), slot)
    c = _client(ready_org.user)
    c.cookies["django_language"] = "sw"
    body = c.get(reverse("organization-applicants", args=[slot.id])).content.decode()
    assert "80% Imejaa" in body and "%%" not in body


# ── 2. stipend reads "TZS 300,000 / month" ─────────────────────────────────────

def test_money_filter():
    from apps.core.templatetags.money_tags import money

    assert money("300000.00") == "300,000"
    assert money(1250000) == "1,250,000"
    assert money(None) is None


@pytest.mark.django_db
def test_stipend_is_shown_as_whole_shillings_with_separator(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Paid intern", capacity=2, stipend_available=True, stipend_amount=300000)
    mine = _client(ready_org.user).get(reverse("organization-slots")).content.decode()
    assert "TZS 300,000" in mine and "300000" not in mine and ".00" not in mine
    assert "/ month" in mine and "/mo<" not in mine
    student = _client(_student(places).user)
    assert "TZS 300,000 / month" in student.get(reverse("student-slot-detail", args=[slot.id])).content.decode()
    apply_page = student.get(reverse("student-apply", args=[slot.id])).content.decode()
    assert "TZS 300,000 / month" in apply_page
    sw = _client(ready_org.user)
    sw.cookies["django_language"] = "sw"
    assert "/ mwezi" in sw.get(reverse("organization-slots")).content.decode()


# ── 3. no dangling "•" between meta items ─────────────────────────────────────

@pytest.mark.django_db
def test_slot_meta_line_uses_wrap_safe_separators(ready_org, places):
    _slot(ready_org, places["kinondoni"], "Intern", capacity=2, department="Computer Science")
    body = _client(ready_org.user).get(reverse("organization-slots")).content.decode()
    assert body.count("ipt-meta-item") >= 4
    assert "•" not in body  # no hand-written bullets left between items (they dangled at wrapped line ends)
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "static" / "css" / "responsive.css").read_text(encoding="utf-8")
    assert ".ipt-meta { overflow: hidden; }" in css and r'content: "\2022"' in css
