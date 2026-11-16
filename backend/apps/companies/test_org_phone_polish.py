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


# ── 4. slot Applicants filters fit side by side on a phone ─────────────────────

@pytest.mark.django_db
def test_slot_applicants_filters_use_short_labels(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=3)
    body = _applicants_page(ready_org, places, slot)
    assert 'class="ipt-compact-filters' in body
    for short in ("Any level", "Newest paid", "Oldest paid", ">Education level<"):
        assert short in body, short
    for long in ("Any qualification", "Newest paid first", "Earliest paid first", "Filter by Education Level"):
        assert long not in body, long


@pytest.mark.django_db
def test_slot_applicants_header_has_no_trailing_separator(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=3)
    body = _applicants_page(ready_org, places, slot)
    head = body[body.index("<h1") : body.index("</section>", body.index("<h1"))]
    assert head.count("ipt-meta-item") == 3 and "w-1 h-1 rounded-full" not in head


# ── 5. My Slots: two labelled buttons + a "More" menu with text labels ─────────

@pytest.mark.django_db
def test_my_slots_actions_are_labelled_with_a_more_menu(ready_org, places):
    empty = _slot(ready_org, places["kinondoni"], "No applicants yet", capacity=2)
    busy = _slot(ready_org, places["kinondoni"], "Has applicants", capacity=2)
    _pay(_student(places), busy)
    c = _client(ready_org.user)
    body = c.get(reverse("organization-slots")).content.decode()
    assert body.count('class="relative ipt-more"') == 2
    for label in ("Applicants", "Edit", "More", "Pause", "Extend deadline", "Close to new applications", "Duplicate (post again)"):
        assert f">{label}<" in body.replace("</span>", "<").replace("\n", "") or label in body, label
    assert "p-1.5 text-on-surface-variant" not in body  # the unlabelled icon buttons are gone
    # Delete is the last item of the menu, red, with its confirm; a slot with applications explains why it cannot be deleted.
    delete_url = reverse("organization-slot-delete", args=[empty.id])
    assert f'action="{delete_url}"' in body and "return confirm(" in body
    assert reverse("organization-slot-delete", args=[busy.id]) not in body
    assert body.count("cannot be deleted") == 1
    # The actions still work: pause through the menu's form.
    assert c.post(reverse("organization-slot-toggle", args=[empty.id])).status_code == 302
    empty.refresh_from_db()
    assert empty.status == "PAUSED"
