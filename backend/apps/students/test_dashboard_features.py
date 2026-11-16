"""Student dashboard features: the apply rule without admin approval, next step, completeness,
recommendations, saved slots, saved-search alerts, notifications centre, acceptance letter, reviews."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import UserRole
from apps.applications.models import Application, OrganizationReview
from apps.applications.services import accept_and_notify, create_application, finalize_verified_payment
from apps.applications.tasks import remind_expiring
from apps.companies.services import create_organization
from apps.locations.models import District, Region
from apps.notifications.models import Notification, NotificationType
from apps.payments.models import Payment, PaymentMethod
from apps.slots.models import Slot, SlotStatus
from apps.students import dashboard as dash
from apps.students.models import (
    SavedSearch,
    SavedSlot,
    StudentProfile,
    University,
    VerificationStatus,
    student_can_apply,
)
from apps.students.tasks import match_saved_searches

User = get_user_model()
PW = "Secret123!x"


@pytest.fixture(autouse=True)
def _reset_redis_state():
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield


def _client(user):
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def places(db):
    dar = Region.objects.create(name="Dar es Salaam", slug="dar")
    arusha = Region.objects.create(name="Arusha", slug="arusha")
    return {
        "dar": dar,
        "arusha": arusha,
        "kinondoni": District.objects.create(name="Kinondoni", region=dar),
        "ilala": District.objects.create(name="Ilala", region=dar),
        "meru": District.objects.create(name="Meru", region=arusha),
    }


@pytest.fixture
def org(db):
    owner = User.objects.create_user(
        email="owner@o.tz", password=PW, role=UserRole.ORGANIZATION, email_verified=True
    )
    organization = create_organization(owner, "Acme Ltd", email="hr@acme.tz", phone="255700000001")
    organization.industry = "Technology"
    organization.verification_status = VerificationStatus.APPROVED
    organization.save()
    return organization


def _student(places, email="stu@x.tz", *, phone_verified=True, complete=True, **extra):
    user = User.objects.create_user(
        email=email, password=PW, role=UserRole.STUDENT, email_verified=True,
        phone="2557" + str(abs(hash(email)) % 10**8).zfill(8), phone_verified=phone_verified,
        first_name="Asha", last_name="Juma",
    )
    fields = dict(
        user=user, university=University.objects.get_or_create(name="UDSM")[0],
        course="Computer Science", education_level=8, academic_year=3,
    )
    if complete:
        fields.update(region=places["dar"], district=places["kinondoni"])
    fields.update(extra)
    return StudentProfile.objects.create(**fields)


def _slot(org, district, title="Software Intern", **extra):
    fields = dict(
        organization=org, title=title, industry="Technology", role_type="Internship",
        district=district, capacity=2, department="Computer Science",
    )
    fields.update(extra)
    return Slot.objects.create(**fields)


def _pay(student, slot):
    app = create_application(student, slot)
    payment = Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id=f"GW-{app.id}")
    Application.objects.filter(pk=app.pk).update(can_cancel=False)  # past the 24h cancel window
    return Application.objects.select_related("payment", "student__user", "slot__organization").get(pk=app.pk)


# ── apply rule: no platform-admin approval ──────────────────────────────────

@pytest.mark.django_db
def test_apply_needs_complete_profile_and_verified_phone_not_admin_approval(places, org):
    slot = _slot(org, places["kinondoni"])
    # Admin "rejected"/"pending" no longer blocks a student.
    rejected = _student(places, "rejected@x.tz", verification_status=VerificationStatus.REJECTED)
    assert student_can_apply(rejected.user)
    assert _client(rejected.user).get(reverse("student-apply", args=[slot.id])).status_code == 200

    incomplete = _student(places, "incomplete@x.tz", complete=False)
    assert not student_can_apply(incomplete.user)
    resp = _client(incomplete.user).get(reverse("student-apply", args=[slot.id]))
    assert resp.status_code == 302 and resp.url == reverse("student-profile")

    no_phone = _student(places, "nophone@x.tz", phone_verified=False)
    assert not student_can_apply(no_phone.user)
    resp = _client(no_phone.user).get(reverse("student-apply", args=[slot.id]))
    assert resp.status_code == 302 and resp.url == reverse("account-security")

    market = _client(no_phone.user).get(reverse("student-marketplace"))
    assert market.status_code == 200 and market.context["can_apply"] is False


# ── dashboard: next step, completeness, stats ───────────────────────────────

@pytest.mark.django_db
def test_next_step_priorities(places, org):
    slot = _slot(org, places["kinondoni"])
    incomplete = _student(places, "a@x.tz", complete=False)
    assert dash.next_step(incomplete.user, incomplete, [])["kind"] == "profile"

    no_phone = _student(places, "b@x.tz", phone_verified=False)
    assert dash.next_step(no_phone.user, no_phone, [])["kind"] == "phone"

    ready = _student(places, "c@x.tz")
    assert dash.next_step(ready.user, ready, [])["kind"] == "apply"

    # An unpaid seat beats everything else, and carries the countdown.
    app = create_application(ready, slot)
    Payment.objects.create(student=ready, application=app, amount=15000, method=PaymentMethod.M_PESA)
    apps = list(Application.objects.filter(student=ready).select_related("slot__organization", "payment"))
    step = dash.next_step(ready.user, ready, apps)
    assert step["kind"] == "pay" and step["countdown"] == app.payment_deadline

    credit = _student(places, "d@x.tz", reapplication_credit=True)
    assert dash.next_step(credit.user, credit, [])["kind"] == "credit"


@pytest.mark.django_db
def test_find_a_slot_shows_the_next_step_and_the_profile_page_shows_completeness(places, org):
    slot = _slot(org, places["kinondoni"])
    student = _student(places)
    app = create_application(student, slot)
    Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    c = _client(student.user)
    page = c.get(reverse("student-marketplace"))
    body = page.content.decode()
    assert page.status_code == 200
    assert "Pay now to keep your seat" in body and "data-countdown" in body  # the urgent step is on the landing page
    assert page.context["next_step"]["kind"] == "pay"
    # My Applications already shows the pay countdown on the card, so the banner is not repeated there
    assert c.get(reverse("student-applications")).context["next_step"] is None
    profile = c.get(reverse("student-profile"))
    assert "Profile completeness" in profile.content.decode() and profile.context["completeness"]["percent"] > 0

    # A student with no profile at all still gets working pages: the banner asks to complete it.
    bare = User.objects.create_user(email="bare@x.tz", password=PW, role=UserRole.STUDENT, email_verified=True)
    cb = _client(bare)
    page = cb.get(reverse("student-marketplace"))
    assert page.status_code == 200 and page.context["next_step"]["kind"] == "profile"
    assert "Complete your profile to start applying" in page.content.decode()
    assert cb.get(reverse("student-applications")).context["next_step"]["kind"] == "profile"
    assert cb.get(reverse("student-profile")).context["completeness"]["percent"] == 0


def test_ready_student_sees_no_banner_on_find_a_slot(places):
    student = _student(places)
    page = _client(student.user).get(reverse("student-marketplace"))
    assert page.context["next_step"] is None  # "you are ready to apply" is not shown to someone already browsing slots


@pytest.mark.django_db
def test_profile_completeness_ignores_admin_approval(places):
    student = _student(places, verification_status=VerificationStatus.PENDING)
    result = dash.profile_completeness(student.user, student)
    labels = [item["label"] for item in result["items"]]
    assert not any("approv" in label.lower() or "verification" in label.lower() for label in labels)
    done = {item["label"] for item in result["items"] if item["done"]}
    assert {"Course and education level", "Region and district", "University", "Phone number verified"} <= done
    assert 0 < result["percent"] < 100


# ── recommendations, closing soon, new nearby ───────────────────────────────

@pytest.mark.django_db
def test_recommendations_match_level_location_and_course(places, org):
    student = _student(places)  # level 8, year 3, Kinondoni (Dar), Computer Science
    best = _slot(org, places["kinondoni"], "Best", education_level=8, academic_year=3)
    region_only = _slot(org, places["ilala"], "Region only", department="Accounting", industry="Finance")
    too_high = _slot(org, places["kinondoni"], "Masters only", education_level=9)
    wrong_year = _slot(org, places["kinondoni"], "Year one", academic_year=1)
    far_unrelated = _slot(org, places["meru"], "Far", department="Accounting", industry="Finance")
    paused = _slot(org, places["kinondoni"], "Paused", status=SlotStatus.PAUSED)
    closed = _slot(org, places["kinondoni"], "Deadline passed", application_deadline=timezone.now() - timedelta(days=1))

    results = dash.recommended_slots(student)
    titles = [s.title for s in results]
    assert titles[0] == "Best" and results[0].match == 100
    assert "Region only" in titles
    for excluded in ("Masters only", "Year one", "Far", "Paused", "Deadline passed"):
        assert excluded not in titles
    assert "In your district" in results[0].reasons

    # A slot the student already holds a seat for is no longer recommended.
    create_application(student, best)
    assert "Best" not in [s.title for s in dash.recommended_slots(student)]

    # Recommendations live at the top of Find a Slot; the old page just leads there.
    c = _client(student.user)
    assert c.get(reverse("student-recommended")).url == reverse("student-marketplace")
    market = c.get(reverse("student-marketplace"))
    assert "Region only" in [row["title"] for row in market.context["recommended"]]
    assert too_high and wrong_year and far_unrelated and paused and closed


@pytest.mark.django_db
def test_closing_soon_and_new_nearby(places, org):
    student = _student(places)
    _slot(org, places["ilala"], "Closing", application_deadline=timezone.now() + timedelta(days=2))
    _slot(org, places["ilala"], "Later", application_deadline=timezone.now() + timedelta(days=30))
    _slot(org, places["meru"], "Other region")
    assert [s.title for s in dash.closing_soon_slots(student)] == ["Closing"]
    nearby = {s.title for s in dash.new_nearby_slots(student)}
    assert nearby == {"Closing", "Later"}
    # "Closing soon" is a quick filter inside Find a Slot now.
    resp = _client(student.user).get(reverse("student-discover"))
    assert resp.status_code == 302 and resp.url == reverse("student-marketplace") + "?quick=closing"


# ── saved slots and saved-search alerts ─────────────────────────────────────

@pytest.mark.django_db
def test_save_and_unsave_a_slot(places, org):
    student = _student(places)
    slot = _slot(org, places["kinondoni"])
    c = _client(student.user)
    url = reverse("student-toggle-saved-slot", args=[slot.id])
    assert c.post(url, HTTP_ACCEPT="application/json").json() == {"saved": True}
    assert SavedSlot.objects.filter(student=student, slot=slot).exists()
    assert slot.title in c.get(reverse("student-saved-slots")).content.decode()
    assert c.post(url, HTTP_ACCEPT="application/json").json() == {"saved": False}
    assert not SavedSlot.objects.exists()
    # Organizations cannot use the student endpoint.
    assert _client(org.user).post(url).status_code == 403


@pytest.mark.django_db
def test_saved_search_alert_notifies_once_when_a_matching_slot_is_posted(places, org):
    student = _student(places)
    other = _student(places, "other@x.tz")
    c = _client(student.user)
    alerts_url = reverse("student-alerts")

    assert c.post(alerts_url, {}, HTTP_ACCEPT="application/json").status_code == 400  # no filter chosen
    saved = c.post(alerts_url, {"region": str(places["dar"].id), "department": "Computer"}, HTTP_ACCEPT="application/json")
    assert saved.status_code == 200 and saved.json()["saved"] is True
    assert c.post(alerts_url, {"region": str(places["dar"].id), "department": "Computer"}, HTTP_ACCEPT="application/json").status_code == 400
    SavedSearch.objects.create(student=other, region=places["arusha"])

    no_match = _slot(org, places["meru"], "Arusha accounting", department="Accounting")
    assert match_saved_searches(str(no_match.id))["notified"] == 1  # only `other` (Arusha)
    assert not Notification.objects.filter(user=student.user, type=NotificationType.SLOT_ALERT).exists()

    slot = _slot(org, places["ilala"], "Data Intern")
    assert match_saved_searches(str(slot.id))["notified"] == 1
    note = Notification.objects.get(user=student.user, type=NotificationType.SLOT_ALERT)
    assert "Data Intern" in note.title and str(slot.id) in note.link
    # Reopening the slot must not notify again.
    assert match_saved_searches(str(slot.id))["notified"] == 0
    assert SavedSearch.objects.get(student=student).last_matched_at is not None

    # Alerts are listed on the Notifications page; the old address leads there.
    assert c.get(alerts_url).url == reverse("notifications") + "#alerts"
    page = c.get(reverse("notifications"))
    assert page.status_code == 200 and "Computer" in page.content.decode()
    alert = SavedSearch.objects.get(student=student)
    # Another student cannot delete my alert.
    assert _client(other.user).post(alerts_url, {"action": "delete", "alert_id": str(alert.id)}).status_code == 404
    c.post(alerts_url, {"action": "delete", "alert_id": str(alert.id)})
    assert not SavedSearch.objects.filter(student=student).exists()


# ── notifications centre ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_notification_centre_is_private_to_each_user(places):
    me = _student(places).user
    other = _student(places, "other@x.tz").user
    mine = Notification.objects.create(user=me, type=NotificationType.SYSTEM, title="Mine", body="b", link="/student/applications/")
    theirs = Notification.objects.create(user=other, type=NotificationType.SYSTEM, title="Theirs", body="b")
    c = _client(me)

    page = c.get(reverse("notifications"))
    body = page.content.decode()
    assert page.status_code == 200 and "Mine" in body and "Theirs" not in body
    summary = c.get(reverse("notifications-summary")).json()
    assert summary["unread"] == 1 and [n["title"] for n in summary["latest"]] == ["Mine"]

    for name in ("notification-open", "notification-mark", "notification-delete"):
        url = reverse(name, args=[theirs.id])
        resp = c.get(url) if name == "notification-open" else c.post(url)
        assert resp.status_code == 404
    theirs.refresh_from_db()
    assert not theirs.is_read

    opened = c.get(reverse("notification-open", args=[mine.id]))
    assert opened.status_code == 302 and opened.url == "/student/applications/"
    mine.refresh_from_db()
    assert mine.is_read
    assert c.get(reverse("notifications"), {"status": "unread"}).context["notifications"].paginator.count == 0

    c.post(reverse("notifications-clear-read"))
    assert not Notification.objects.filter(user=me).exists()
    assert Notification.objects.filter(user=other).count() == 1

    # A notification can never send the user to another site.
    evil = Notification.objects.create(user=me, type=NotificationType.SYSTEM, title="x", body="b", link="https://evil.example/")
    assert c.get(reverse("notification-open", args=[evil.id])).url == reverse("notifications")
    assert Client().get(reverse("notifications")).status_code == 302  # login required


@pytest.mark.django_db
def test_expiring_seat_reminder_is_sent_once(places, org):
    student = _student(places)
    app = create_application(student, _slot(org, places["kinondoni"]))
    Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    assert remind_expiring() == {"reminded": 0}  # three hours left: too early
    Application.objects.filter(pk=app.pk).update(payment_deadline=timezone.now() + timedelta(minutes=30))
    assert remind_expiring() == {"reminded": 1}
    assert remind_expiring() == {"reminded": 0}
    note = Notification.objects.get(user=student.user, type=NotificationType.PAYMENT)
    assert note.link == f"/student/payments/{app.payment.id}/"


# ── after acceptance: letter, checklist, review, organization page ──────────

@pytest.mark.django_db
def test_acceptance_letter_checklist_and_review(places, org):
    student = _student(places)
    other = _student(places, "other@x.tz")
    app = _pay(student, _slot(org, places["kinondoni"]))
    c = _client(student.user)
    letter_url = reverse("student-acceptance-letter", args=[app.id])
    review_url = reverse("student-review", args=[app.id])

    # Nothing is available before the organization accepts.
    assert c.get(letter_url).status_code == 404
    assert c.get(reverse("student-checklist", args=[app.id])).status_code == 404
    assert c.post(review_url, {"rating": "5"}).status_code == 404

    accept_and_notify(app, company_message="Report Monday 8 AM", actor=org.user)
    letter = c.get(letter_url)
    assert letter.status_code == 200 and letter["Content-Type"] == "application/pdf"
    assert letter.content.startswith(b"%PDF") and "attachment" in letter["Content-Disposition"]
    assert _client(other.user).get(letter_url).status_code == 404  # not their application

    checklist = c.get(reverse("student-checklist", args=[app.id])).content.decode()
    assert "Report Monday 8 AM" in checklist and "hr@acme.tz" in checklist

    applications = c.get(reverse("student-applications")).content.decode()
    assert "Report Monday 8 AM" in applications and "Acceptance letter" in applications

    assert c.post(review_url, {"rating": "9"}).status_code == 200  # invalid rating: form shown again
    assert not OrganizationReview.objects.exists()
    assert c.post(review_url, {"rating": "4", "comment": "Good supervision"}).status_code == 302
    c.post(review_url, {"rating": "5", "comment": "Even better"})
    review = OrganizationReview.objects.get()
    assert review.rating == 5 and review.organization_id == org.id
    assert Notification.objects.filter(user=org.user, type=NotificationType.REVIEW).count() == 1

    page = c.get(reverse("organization-public-profile", args=[org.id]))
    body = page.content.decode()
    assert page.status_code == 200 and page.context["average"] == 5.0
    assert "Even better" in body and "Asha" not in body.split("What students say")[1]  # reviews are anonymous


# ── payment page (phone review): countdown, number without a doubled +255, amount with a separator ──

@pytest.mark.django_db
def test_payment_page_shows_the_seat_countdown_a_clean_phone_number_and_a_formatted_amount(places, org):
    slot = _slot(org, places["kinondoni"])
    student = _student(places)
    student.user.phone = "255791099136"
    student.user.save()
    app = create_application(student, slot)
    payment = Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    body = _client(student.user).get(reverse("student-payment", args=[payment.id])).content.decode()
    assert "Seat released in" in body and "data-countdown=" in body  # the 3-hour hold is visible while paying
    assert 'value="791 099 136"' in body and 'value="255791099136"' not in body  # "+255" is the prefix, not part of the value
    assert 'text-primary">15,000</span>' in body  # the amount the student reads (the JS variable keeps the raw value)


def test_phone_filters():
    from apps.core.templatetags.phone_tags import phone_intl, phone_local

    assert phone_local("255791099136") == "791 099 136"
    assert phone_local("+255 791 099 136") == "791 099 136"
    assert phone_local("0791099136") == "791 099 136"
    assert phone_local("") == ""
    assert phone_intl("255700000001") == "+255 700 000 001"
    assert phone_intl("12345") == "12345"


@pytest.mark.django_db
def test_application_fee_is_written_with_a_thousands_separator_everywhere(places, org):
    slot = _slot(org, places["kinondoni"])
    student = _student(places)
    app = create_application(student, slot)
    Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    other = _slot(org, places["kinondoni"], "Another")
    c = _client(student.user)
    for url in (reverse("student-applications"), reverse("student-slot-detail", args=[other.id]), reverse("student-apply", args=[other.id])):
        body = c.get(url).content.decode()
        assert "TZS 15,000" in body and "15000.00" not in body, url


@pytest.mark.django_db
def test_slot_location_does_not_repeat_the_region(places, org):
    """District's own text is already "Kinondoni, Dar es Salaam"; pages used to append the region again."""
    slot = _slot(org, places["kinondoni"])
    student = _student(places)
    c = _client(student.user)
    app = create_application(student, slot)
    for url in (reverse("student-slot-detail", args=[slot.id]), reverse("student-apply", args=[_slot(org, places["kinondoni"], "Second").id])):
        body = c.get(url).content.decode()
        assert "Dar es Salaam, Dar es Salaam" not in body and "Dar es Salaam · Dar es Salaam" not in body, url
        assert "Kinondoni, Dar es Salaam" in body, url
