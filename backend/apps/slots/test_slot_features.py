"""Slot posting and applying: pause/close rules, eligibility, credit re-application, cancel notice,
industry default, capacity guard, slot detail + views, search tools, apply rules checkbox, release seat,
application tabs, placement details, deadline extend/close, edit notices, applicant sort and the funnel."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.applications.models import Application, ApplicationStatus
from apps.applications.services import (
    SlotFullError,
    accept_and_notify,
    create_application,
    finalize_verified_payment,
)
from apps.companies import dashboard as company_dash
from apps.companies.forms import SlotForm
from apps.companies.test_dashboard_features import _make_org, _paid
from apps.locations.models import District, Region
from apps.notifications.models import Notification
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.slots.models import Slot, SlotStatus, SlotView
from apps.slots.views import _load_slot_search, clean_min_stipend
from apps.students.test_dashboard_features import _student

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_redis_state():
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield


@pytest.fixture
def places(db):
    dar = Region.objects.create(name="Dar es Salaam", slug="dar")
    return {
        "dar": dar,
        "kinondoni": District.objects.create(name="Kinondoni", region=dar),
        "ilala": District.objects.create(name="Ilala", region=dar),
    }


@pytest.fixture
def org_owner(db):
    return _make_org("Alpha Ltd", "alpha")


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _slot(org, district, title="Intern", **extra):
    fields = dict(
        organization=org, title=title, industry="Tech", role_type="Internship", district=district, capacity=2
    )
    fields.update(extra)
    return Slot.objects.create(**fields)


def _pending(student, slot):
    return create_application(student, slot)


def _paid_cancellable(student, slot):
    """Paid just now, so the 24-hour cancel window is open."""
    app = create_application(student, slot)
    payment = Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id=f"GW-{app.id}")
    return Application.objects.select_related("payment").get(pk=app.pk)


def _letter():
    return SimpleUploadedFile("intro.pdf", b"%PDF-1.4 letter", content_type="application/pdf")


def _slot_post(slot, **over):
    data = {
        "title": slot.title, "industry": slot.industry, "description": "", "role_type": slot.role_type,
        "region": slot.district.region_id, "district": slot.district_id, "department": "",
        "education_level": "", "academic_year": "", "application_deadline": "", "start_date": "",
        "duration_weeks": "", "reporting_contact": "", "capacity": slot.capacity, "skills_required": "",
    }
    if slot.stipend_available:
        data["stipend_available"] = "on"
        data["stipend_amount"] = str(slot.stipend_amount)
    data.update(over)
    return data


# ── Part 1: fixes ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_paused_slot_is_hidden_and_refuses_applications(places, org_owner):
    org, _ = org_owner
    paused = _slot(org, places["kinondoni"], "Paused", status=SlotStatus.PAUSED)
    _slot(org, places["kinondoni"], "Live")
    titles = {row["title"] for row in _load_slot_search(None, None, None, None, None)}
    assert titles == {"Live"}
    with pytest.raises(SlotFullError, match="not accepting"):
        create_application(_student(places, "a@x.tz"), paused)
    assert not Application.objects.exists()


@pytest.mark.django_db
def test_refresh_status_never_reopens_a_paused_or_closed_slot(places, org_owner):
    org, _ = org_owner
    for status in (SlotStatus.PAUSED, SlotStatus.CLOSED):
        slot = _slot(org, places["kinondoni"], status=status)
        slot.refresh_status()
        slot.refresh_from_db()
        assert slot.status == status


@pytest.mark.django_db
def test_resuming_a_paused_full_slot_makes_it_full_not_open(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], capacity=1)
    _pending(_student(places, "a@x.tz"), slot)
    c = _client(owner)
    c.post(reverse("organization-slot-toggle", args=[slot.id]))
    slot.refresh_from_db()
    assert slot.status == SlotStatus.PAUSED
    c.post(reverse("organization-slot-toggle", args=[slot.id]))
    slot.refresh_from_db()
    assert slot.status == SlotStatus.FULL


@pytest.mark.django_db
def test_credit_reapplication_completes_through_normal_payment_finalization(places, org_owner):
    org, owner = org_owner
    first = _slot(org, places["kinondoni"], "First")
    second = _slot(org, places["ilala"], "Second")
    student = _student(places, "credit@x.tz")
    app = _paid_cancellable(student, first)
    c = _client(student.user)
    c.post(reverse("student-cancel-application", args=[app.id]))
    student.refresh_from_db()
    assert student.reapplication_credit

    resp = c.post(reverse("student-apply", args=[second.id]), {"application_letter": _letter(), "accept_rules": "on"})
    assert resp.status_code == 302 and resp.url == reverse("student-applications")
    reapplied = Application.objects.get(student=student, slot=second)
    assert reapplied.status == ApplicationStatus.PAID and reapplied.can_cancel is False
    assert reapplied.payment.is_paid and reapplied.payment.gateway_txn_id == "CREDIT-REAPPLICATION"
    student.refresh_from_db()
    assert not student.reapplication_credit
    assert Notification.objects.filter(user=owner, title="New Applicant", body__contains="Second").exists()


@pytest.mark.django_db
def test_cancelling_notifies_the_organization(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"])
    student = _student(places, "cancel@x.tz")
    app = _paid_cancellable(student, slot)
    _client(student.user).post(reverse("student-cancel-application", args=[app.id]))
    note = Notification.objects.get(user=owner, title="Applicant cancelled")
    assert "Asha Juma" in note.body and slot.title in note.body
    assert note.link == f"/organization/slots/{slot.id}/applicants/"


@pytest.mark.django_db
def test_student_below_required_level_cannot_apply(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], education_level=8)
    low = _student(places, "low@x.tz", education_level=6)
    with pytest.raises(SlotFullError, match="Level 8"):
        create_application(low, slot)
    resp = _client(low.user).get(reverse("student-apply", args=[slot.id]))
    assert resp.status_code == 302 and resp.url == reverse("student-marketplace")
    # A higher level is fine (the slot level is a minimum), and so is an unknown level.
    assert create_application(_student(places, "high@x.tz", education_level=9), slot).pk
    assert create_application(_student(places, "unk@x.tz", education_level=None), slot).pk


@pytest.mark.django_db
def test_higher_diploma_slot_checks_the_academic_year(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], education_level=7, academic_year=2, capacity=5)
    with pytest.raises(SlotFullError, match="students"):
        create_application(_student(places, "y1@x.tz", education_level=7, academic_year=1), slot)
    assert create_application(_student(places, "y2@x.tz", education_level=7, academic_year=2), slot).pk
    # A bachelor's student is not held to the diploma year.
    assert create_application(_student(places, "b@x.tz", education_level=8, academic_year=3), slot).pk


@pytest.mark.django_db
def test_slot_industry_defaults_to_the_organization_industry(places, org_owner):
    org, owner = org_owner
    data = _slot_post(_slot(org, places["kinondoni"], industry=""), title="New one", industry="")
    resp = _client(owner).post(reverse("organization-slot-create"), data)
    assert resp.status_code == 302
    assert Slot.objects.get(title="New one").industry == org.industry
    data = _slot_post(_slot(org, places["kinondoni"]), title="Custom", industry="Mining")
    _client(owner).post(reverse("organization-slot-create"), data)
    assert Slot.objects.get(title="Custom").industry == "Mining"


@pytest.mark.django_db
def test_capacity_cannot_drop_below_booked_seats(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], capacity=3)
    _pending(_student(places, "a@x.tz"), slot)
    _pending(_student(places, "b@x.tz"), slot)
    form = SlotForm(_slot_post(slot, capacity=1), instance=slot)
    assert not form.is_valid() and "capacity" in form.errors
    assert SlotForm(_slot_post(slot, capacity=2), instance=slot).is_valid()
    assert not SlotForm(_slot_post(slot, capacity=0), instance=Slot()).is_valid()


@pytest.mark.django_db
def test_changing_capacity_refreshes_the_slot_status(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], capacity=2)
    _pending(_student(places, "a@x.tz"), slot)
    _pending(_student(places, "b@x.tz"), slot)
    slot.refresh_status()
    assert Slot.objects.get(pk=slot.pk).status == SlotStatus.FULL
    _client(owner).post(reverse("organization-slot-edit", args=[slot.id]), _slot_post(slot, capacity=4))
    assert Slot.objects.get(pk=slot.pk).status == SlotStatus.OPEN


@pytest.mark.django_db
def test_my_slots_counts_seats_held_and_paid_applicants_only(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], capacity=4)
    _paid(slot, "paid@x.tz")
    expired = _pending(_student(places, "exp@x.tz"), slot)
    Application.objects.filter(pk=expired.pk).update(status=ApplicationStatus.UNPAID)
    ctx = _client(owner).get(reverse("organization-slots")).context
    assert ctx["total_capacity"] == 4 and ctx["total_filled"] == 1 and ctx["total_applications"] == 1
    assert ctx["slots"][0].seats_left == 3


# ── A: slot detail page and views ───────────────────────────────────────────


@pytest.mark.django_db
def test_slot_detail_shows_rules_and_counts_each_student_once(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], description="Build things", skills_required=["Python"])
    student = _student(places, "v@x.tz")
    c = _client(student.user)
    for _ in range(3):
        resp = c.get(reverse("student-slot-detail", args=[slot.id]))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Build things" in body and "Python" in body and "How applying works" in body
    assert SlotView.objects.filter(slot=slot).count() == 1
    assert resp.context["fee"] and resp.context["payment_hours"] == 3


@pytest.mark.django_db
def test_slot_detail_is_for_students_and_explains_why_it_is_closed(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], status=SlotStatus.PAUSED)
    assert _client(owner).get(reverse("student-slot-detail", args=[slot.id])).status_code == 403
    resp = _client(_student(places, "p@x.tz").user).get(reverse("student-slot-detail", args=[slot.id]))
    assert "paused" in resp.context["closed_reason"]
    low = _student(places, "low@x.tz", education_level=5)
    gated = _slot(org, places["kinondoni"], "Gated", education_level=8)
    assert "Level 8" in _client(low.user).get(reverse("student-slot-detail", args=[gated.id])).context["blocker"]


# ── B: search tools ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_search_by_keyword_in_title_description_and_skills(places, org_owner):
    org, _ = org_owner
    _slot(org, places["kinondoni"], "Accounts clerk", description="Ledgers")
    _slot(org, places["kinondoni"], "Developer", skills_required=["Python", "SQL"])
    _slot(org, places["kinondoni"], "Driver")

    def titles(**kw):
        return {r["title"] for r in _load_slot_search(None, None, None, None, None, **kw)}

    assert titles(keyword="python") == {"Developer"}
    assert titles(keyword="ledger") == {"Accounts clerk"}
    assert titles(keyword="driv") == {"Driver"}


@pytest.mark.django_db
def test_search_stipend_filters_and_sorting(places, org_owner):
    org, _ = org_owner
    _slot(org, places["kinondoni"], "Unpaid")
    _slot(org, places["kinondoni"], "Low", stipend_available=True, stipend_amount=100000)
    _slot(org, places["kinondoni"], "High", stipend_available=True, stipend_amount=500000)

    def titles(**kw):
        return [r["title"] for r in _load_slot_search(None, None, None, None, None, **kw)]

    assert set(titles(paid_only=True)) == {"Low", "High"}
    assert titles(min_stipend="200000") == ["High"]
    assert titles(sort="stipend")[:2] == ["High", "Low"]


@pytest.mark.django_db
def test_search_sorts_by_closing_date_and_seats(places, org_owner):
    org, _ = org_owner
    soon = _slot(org, places["kinondoni"], "Soon", application_deadline=timezone.now() + timedelta(days=2))
    _slot(org, places["kinondoni"], "Later", application_deadline=timezone.now() + timedelta(days=20))
    _slot(org, places["kinondoni"], "NoDeadline")
    order = [r["title"] for r in _load_slot_search(None, None, None, None, None, sort="closing")]
    assert order == ["Soon", "Later", "NoDeadline"]
    big = _slot(org, places["kinondoni"], "Big", capacity=9)
    assert [r["title"] for r in _load_slot_search(None, None, None, None, None, sort="seats")][0] == "Big"
    assert big.pk


def test_min_stipend_input_is_sanitised():
    assert clean_min_stipend("250000") == "250000"
    assert clean_min_stipend("1e9999") == ""
    assert clean_min_stipend("abc") == "" and clean_min_stipend("-5") == "" and clean_min_stipend("") == ""
    assert clean_min_stipend("nan") == "" and clean_min_stipend("999999999999") == ""


@pytest.mark.django_db
def test_search_api_accepts_the_new_parameters(places, org_owner):
    org, _ = org_owner
    _slot(org, places["kinondoni"], "Paid one", stipend_available=True, stipend_amount=300000)
    _slot(org, places["kinondoni"], "Unpaid one")
    student = _student(places, "s@x.tz")
    resp = _client(student.user).get("/api/slots/search/", {"paid_only": "1", "sort": "stipend", "q": "paid"})
    assert resp.status_code == 200
    assert [r["title"] for r in resp.json()["results"]] == ["Paid one"]
    bad = _client(student.user).get("/api/slots/search/", {"sort": "'; drop", "min_stipend": "x" * 50})
    assert bad.status_code == 200 and bad.json()["count"] == 2


# ── C: apply rules checkbox ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_apply_needs_the_rules_checkbox(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"])
    student = _student(places, "r@x.tz")
    c = _client(student.user)
    page = c.get(reverse("student-apply", args=[slot.id]))
    assert b"Before you apply" in page.content and page.context["payment_hours"] == 3
    resp = c.post(reverse("student-apply", args=[slot.id]), {"application_letter": _letter()})
    assert resp.status_code == 200 and "accept_rules" in resp.context["form"].errors
    assert not Application.objects.filter(student=student).exists()
    resp = c.post(reverse("student-apply", args=[slot.id]), {"application_letter": _letter(), "accept_rules": "on"})
    assert resp.status_code == 302 and Application.objects.filter(student=student).exists()


# ── D: tabs ─────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_my_applications_tabs_filter_and_count(places, org_owner):
    org, owner = org_owner
    student = _student(places, "tabs@x.tz")
    s1, s2, s3, s4 = (_slot(org, places["kinondoni"], f"S{i}") for i in range(4))
    awaiting = _pending(student, s1)
    waiting = _paid_cancellable(student, s2)
    accepted = _paid_cancellable(student, s3)
    Application.objects.filter(pk=accepted.pk).update(can_cancel=False)
    accept_and_notify(Application.objects.get(pk=accepted.pk))
    expired = _pending(student, s4)
    Application.objects.filter(pk=expired.pk).update(status=ApplicationStatus.UNPAID)
    c = _client(student.user)

    resp = c.get(reverse("student-applications"))
    assert {t["key"]: t["count"] for t in resp.context["tabs"]} == {
        "all": 4, "awaiting": 1, "paid": 1, "accepted": 1, "closed": 1,
    }

    def ids(tab):
        return {a.pk for a in c.get(reverse("student-applications"), {"tab": tab}).context["applications"]}

    assert ids("awaiting") == {awaiting.pk} and ids("paid") == {waiting.pk}
    assert ids("accepted") == {accepted.pk} and ids("closed") == {expired.pk}
    assert len(ids("all")) == 4 and ids("nonsense") == ids("all")


# ── E: release seat ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_release_seat_frees_it_and_allows_rebooking(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], capacity=1)
    student = _student(places, "rel@x.tz")
    c = _client(student.user)
    c.post(reverse("student-apply", args=[slot.id]), {"application_letter": _letter(), "accept_rules": "on"})
    app = Application.objects.get(student=student)
    assert slot.available_count == 0
    Slot.objects.filter(pk=slot.pk).update(status=SlotStatus.FULL)

    resp = c.post(reverse("student-release-seat", args=[app.id]))
    assert resp.status_code == 302
    app.refresh_from_db()
    slot.refresh_from_db()
    assert app.status == ApplicationStatus.UNPAID and slot.available_count == 1
    assert slot.status == SlotStatus.OPEN
    assert Payment.objects.get(application=app).status == PaymentStatus.FAILED
    # Re-booking works through the same path as an expired seat.
    assert create_application(student, slot).status == ApplicationStatus.PENDING


@pytest.mark.django_db
def test_release_seat_refuses_paid_and_foreign_applications(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"])
    mine = _student(places, "mine@x.tz")
    paid = _paid_cancellable(mine, slot)
    c = _client(mine.user)
    c.post(reverse("student-release-seat", args=[paid.id]))
    paid.refresh_from_db()
    assert paid.status == ApplicationStatus.PAID
    other = _student(places, "other@x.tz")
    pending = _pending(other, slot)
    assert c.post(reverse("student-release-seat", args=[pending.id])).status_code == 404
    pending.refresh_from_db()
    assert pending.status == ApplicationStatus.PENDING
    assert c.get(reverse("student-release-seat", args=[paid.id])).status_code == 302  # GET does nothing


# ── F: placement details ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_placement_label_and_end_date(places, org_owner):
    from datetime import date

    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], start_date=date(2027, 1, 5), duration_weeks=8)
    assert slot.placement_label == "Starts 5 Jan 2027 · 8 weeks"
    assert slot.end_date == date(2027, 3, 2)
    assert _slot(org, places["kinondoni"], duration_weeks=1).placement_label == "1 week"
    assert _slot(org, places["kinondoni"]).placement_label == ""


@pytest.mark.django_db
def test_slot_form_validates_start_date(places, org_owner):
    org, _ = org_owner
    slot = _slot(org, places["kinondoni"])
    yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
    assert "start_date" in SlotForm(_slot_post(slot, start_date=yesterday), instance=slot).errors
    deadline = timezone.now() + timedelta(days=10)
    early = (timezone.localdate() + timedelta(days=3)).isoformat()
    form = SlotForm(
        _slot_post(slot, start_date=early, application_deadline=deadline.strftime("%Y-%m-%dT%H:%M")), instance=slot
    )
    assert "start_date" in form.errors
    ok = (timezone.localdate() + timedelta(days=30)).isoformat()
    assert SlotForm(_slot_post(slot, start_date=ok, duration_weeks=8), instance=slot).is_valid()
    assert "duration_weeks" in SlotForm(_slot_post(slot, duration_weeks=500), instance=slot).errors


@pytest.mark.django_db
def test_reporting_contact_is_only_for_accepted_students(places, org_owner):
    from datetime import date

    org, _ = org_owner
    slot = _slot(
        org, places["kinondoni"], start_date=date(2027, 1, 5), duration_weeks=8, reporting_contact="Ask for Ms Mushi, HR"
    )
    paid = _paid(slot, "acc@x.tz")
    outsider = _student(places, "out@x.tz")
    detail = _client(outsider.user).get(reverse("student-slot-detail", args=[slot.id])).content.decode()
    assert "Starts 5 Jan 2027" in detail and "Ask for Ms Mushi" not in detail
    api = _client(outsider.user).get("/api/slots/search/").json()["results"][0]
    assert "reporting_contact" not in api and api["placement_label"].startswith("Starts")

    waiting_page = _client(paid.student.user).get(reverse("student-applications")).content.decode()
    assert "Ask for Ms Mushi" not in waiting_page
    accept_and_notify(paid)
    done = _client(paid.student.user).get(reverse("student-applications")).content.decode()
    assert "Ask for Ms Mushi" in done
    checklist = _client(paid.student.user).get(reverse("student-checklist", args=[paid.id])).content.decode()
    assert "Ask for Ms Mushi" in checklist and "Your placement" in checklist


@pytest.mark.django_db
def test_acceptance_letter_lists_the_placement(places, org_owner):
    from datetime import date

    from apps.applications.letters import build_acceptance_letter

    org, _ = org_owner
    slot = _slot(org, places["kinondoni"], start_date=date(2027, 1, 5), duration_weeks=8, reporting_contact="HR desk")
    paid = _paid(slot, "pdf@x.tz")
    accept_and_notify(paid)
    paid.refresh_from_db()
    assert build_acceptance_letter(paid).startswith(b"%PDF")


# ── G: deadline lifecycle ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_slot_past_its_deadline_reads_as_applications_closed(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], application_deadline=timezone.now() - timedelta(hours=1))
    assert slot.status == SlotStatus.OPEN and slot.applications_closed
    assert b"Applications closed" in _client(owner).get(reverse("organization-slots")).content
    assert not _slot(org, places["kinondoni"], status=SlotStatus.PAUSED).applications_closed


@pytest.mark.django_db
def test_extend_deadline_from_now_or_from_the_current_deadline(places, org_owner):
    org, owner = org_owner
    c = _client(owner)
    expired = _slot(org, places["kinondoni"], application_deadline=timezone.now() - timedelta(days=3))
    c.post(reverse("organization-slot-extend", args=[expired.id]), {"days": "7"})
    expired.refresh_from_db()
    assert not expired.deadline_passed
    assert timedelta(days=6, hours=23) < expired.application_deadline - timezone.now() < timedelta(days=7, hours=1)

    future = timezone.now() + timedelta(days=5)
    ahead = _slot(org, places["kinondoni"], application_deadline=future)
    c.post(reverse("organization-slot-extend", args=[ahead.id]), {"days": "3"})
    ahead.refresh_from_db()
    assert abs((ahead.application_deadline - (future + timedelta(days=3))).total_seconds()) < 5

    none = _slot(org, places["kinondoni"])
    c.post(reverse("organization-slot-extend", args=[none.id]), {"days": "14"})
    none.refresh_from_db()
    assert none.application_deadline is not None
    unchanged = none.application_deadline
    c.post(reverse("organization-slot-extend", args=[none.id]), {"days": "9999"})
    none.refresh_from_db()
    assert none.application_deadline == unchanged


@pytest.mark.django_db
def test_extend_is_limited_to_the_own_organization(places, org_owner):
    org, owner = org_owner
    other_org, _ = _make_org("Beta Ltd", "beta")
    foreign = _slot(other_org, places["kinondoni"])
    resp = _client(owner).post(reverse("organization-slot-extend", args=[foreign.id]), {"days": "7"})
    assert resp.status_code == 404


@pytest.mark.django_db
def test_close_and_reopen_a_slot(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], capacity=1)
    c = _client(owner)
    c.post(reverse("organization-slot-close", args=[slot.id]))
    slot.refresh_from_db()
    assert slot.status == SlotStatus.CLOSED
    assert not _load_slot_search(None, None, None, None, None)
    with pytest.raises(SlotFullError, match="not accepting"):
        create_application(_student(places, "c@x.tz"), slot)
    c.post(reverse("organization-slot-toggle", args=[slot.id]))  # pausing a closed slot does nothing
    slot.refresh_from_db()
    assert slot.status == SlotStatus.CLOSED
    c.post(reverse("organization-slot-close", args=[slot.id]))
    slot.refresh_from_db()
    assert slot.status == SlotStatus.OPEN


# ── H: edit notices ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_editing_material_details_asks_first_then_notifies_seat_holders(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], stipend_available=True, stipend_amount=100000)
    holder = _student(places, "holder@x.tz")
    _pending(holder, slot)
    c = _client(owner)
    url = reverse("organization-slot-edit", args=[slot.id])

    first = c.post(url, _slot_post(slot, stipend_amount="50000"))
    assert first.status_code == 200 and first.context["pending_changes"]["Stipend"] == (
        "TZS 100,000 per month", "TZS 50,000 per month",
    )
    slot.refresh_from_db()
    assert slot.stipend_amount == 100000
    assert not Notification.objects.filter(user=holder.user, title__startswith="A slot you applied").exists()

    second = c.post(url, _slot_post(slot, stipend_amount="50000", confirm_changes="1"))
    assert second.status_code == 302
    slot.refresh_from_db()
    assert slot.stipend_amount == 50000
    note = Notification.objects.get(user=holder.user, title="A slot you applied to was updated")
    assert "TZS 100,000 per month -> TZS 50,000 per month" in note.body
    assert note.link == f"/student/slots/{slot.id}/"


@pytest.mark.django_db
def test_editing_without_seat_holders_or_with_minor_changes_needs_no_confirmation(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], stipend_available=True, stipend_amount=100000)
    c = _client(owner)
    url = reverse("organization-slot-edit", args=[slot.id])
    assert c.post(url, _slot_post(slot, stipend_amount="70000")).status_code == 302  # nobody holds a seat yet

    holder = _student(places, "h@x.tz")
    _pending(holder, slot)
    slot.refresh_from_db()
    resp = c.post(url, _slot_post(slot, title="Renamed", description="New text"))
    assert resp.status_code == 302
    assert Slot.objects.get(pk=slot.pk).title == "Renamed"
    assert not Notification.objects.filter(user=holder.user, title__startswith="A slot you applied").exists()


@pytest.mark.django_db
def test_changing_the_location_or_level_is_also_material(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"])
    holder = _student(places, "holder@x.tz")
    _pending(holder, slot)
    c = _client(owner)
    url = reverse("organization-slot-edit", args=[slot.id])
    first = c.post(url, _slot_post(slot, district=places["ilala"].pk, education_level="6"))
    assert first.status_code == 200 and set(first.context["pending_changes"]) == {"Location", "Qualification"}
    c.post(url, _slot_post(slot, district=places["ilala"].pk, education_level="6", confirm_changes="1"))
    slot.refresh_from_db()
    assert slot.district_id == places["ilala"].pk and slot.education_level == 6


# ── I: applicant details and sort ───────────────────────────────────────────


@pytest.mark.django_db
def test_applicants_sort_by_earliest_or_newest_payment(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], capacity=3)
    # paid_at is write-protected by a trigger, so the order is simply the order they were paid in.
    first, second = _paid(slot, "first@x.tz"), _paid(slot, "second@x.tz")
    assert first.payment.paid_at < second.payment.paid_at
    c = _client(owner)
    url = reverse("organization-applicants", args=[slot.id])
    newest = [a.pk for a in c.get(url).context["applications"]]
    earliest = [a.pk for a in c.get(url, {"sort": "earliest"}).context["applications"]]
    assert newest == [second.pk, first.pk] and earliest == [first.pk, second.pk]
    assert [a.pk for a in c.get(url, {"sort": "junk"}).context["applications"]] == newest


@pytest.mark.django_db
def test_applicant_card_shows_year_graduation_and_dates(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"])
    app = _paid(slot, "card@x.tz")
    type(app.student).objects.filter(pk=app.student.pk).update(graduation_year=2028)
    body = _client(owner).get(reverse("organization-applicants", args=[slot.id])).content.decode()
    assert "Graduates 2028" in body and "Applied" in body and "Paid" in body


# ── J: funnel ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_slot_funnel_numbers(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"], capacity=6)
    accepted = _paid(slot, "a@x.tz")
    accept_and_notify(accepted)
    paid = _paid(slot, "b@x.tz")
    waiting = _pending(_student(places, "c@x.tz"), slot)
    expired = _pending(_student(places, "d@x.tz"), slot)
    Application.objects.filter(pk=expired.pk).update(status=ApplicationStatus.UNPAID)
    cancelled = _paid_cancellable(_student(places, "e@x.tz"), slot)
    Application.objects.filter(pk=cancelled.pk).update(status=ApplicationStatus.CANCELLED)
    for student in (accepted.student, paid.student, waiting.student, expired.student):
        SlotView.objects.create(slot=slot, student=student)

    funnel = company_dash.slot_funnel(slot)
    counts = {s["label"]: s["count"] for s in funnel["steps"]}
    assert counts == {"Viewed": 4, "Applied": 5, "Paid": 3, "Accepted": 1}
    assert funnel["awaiting"] == 1 and funnel["unpaid"] == 1 and funnel["cancelled"] == 1
    assert funnel["avg_time_to_accept"]
    pcts = {s["label"]: s["pct"] for s in funnel["steps"]}
    assert pcts["Viewed"] is None and pcts["Applied"] is None  # 5 applied of 4 viewed is not a drop-off rate
    assert pcts["Paid"] == 60 and pcts["Accepted"] == 33
    assert waiting.pk


@pytest.mark.django_db
def test_funnel_shows_on_the_applicants_page_even_with_no_applicants(places, org_owner):
    org, owner = org_owner
    slot = _slot(org, places["kinondoni"])
    SlotView.objects.create(slot=slot, student=_student(places, "v@x.tz"))
    resp = _client(owner).get(reverse("organization-applicants", args=[slot.id]))
    assert b"How this slot is doing" in resp.content and b"No applicants yet" in resp.content
    assert resp.context["funnel"]["steps"][0]["count"] == 1


@pytest.mark.django_db
def test_funnel_is_scoped_to_the_organizations_own_slot(places, org_owner):
    org, owner = org_owner
    other_org, _ = _make_org("Beta Ltd", "beta")
    foreign = _slot(other_org, places["kinondoni"])
    assert _client(owner).get(reverse("organization-applicants", args=[foreign.id])).status_code == 404
