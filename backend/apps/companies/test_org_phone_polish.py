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
