"""Organization dashboard features: KPIs and to-do, applicants across slots, bulk accept, private notes,
export, message templates, slot duplicate/delete rules, insights, activity and ratings."""

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import UserRole
from apps.applications.models import ApplicantNote, Application, ApplicationStatus, OrganizationReview
from apps.applications.services import (
    AcceptanceError,
    accept_and_notify,
    create_application,
    finalize_verified_payment,
)
from apps.companies import dashboard as dash
from apps.companies.models import AcceptanceTemplate, OrganizationMemberRole
from apps.companies.services import add_organization_member, create_organization
from apps.core.models import AuditLog
from apps.documents.models import Document
from apps.documents.tests import make_png_bytes
from apps.locations.models import District, Region
from apps.notifications.models import Notification, NotificationType
from apps.payments.models import Payment, PaymentMethod
from apps.slots.models import Slot
from apps.students.models import StudentProfile, University, VerificationStatus

User = get_user_model()
PW = "Secret123!x"


@pytest.fixture(autouse=True)
def _reset_redis_state():
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield


def _verified(user):
    user.email_verified = True
    user.must_change_password = False
    user.save(update_fields=["email_verified", "must_change_password"])
    return user


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _make_org(name, prefix):
    owner = _verified(
        User.objects.create_user(email=f"{prefix}-owner@o.tz", password=PW, role=UserRole.ORGANIZATION)
    )
    org = create_organization(owner, name)
    org.industry = "Tech"
    org.verification_status = VerificationStatus.APPROVED
    org.save()
    for doc_type in (Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE):
        Document.objects.create(
            owner=owner,
            doc_type=doc_type,
            file=SimpleUploadedFile("c.png", make_png_bytes(), content_type="image/png"),
            original_name="c.png",
        )
    return org, owner


@pytest.fixture
def district(db):
    return District.objects.create(name="Kinondoni", region=Region.objects.create(name="Dar", slug="dar"))


@pytest.fixture
def alpha(db):
    return _make_org("Alpha Ltd", "alpha")


@pytest.fixture
def beta(db):
    return _make_org("Beta Ltd", "beta")


def _slot(org, district, title="Intern", capacity=3):
    return Slot.objects.create(
        organization=org, title=title, industry="Tech", role_type="Internship", district=district, capacity=capacity
    )


def _paid(slot, email, *, cooling_off=False):
    """A paid application. By default the 24h cancellation window is over, so it can be accepted."""
    user = User.objects.create_user(
        email=email, password=PW, role=UserRole.STUDENT, email_verified=True, first_name="Stu", last_name=email[:3]
    )
    student = StudentProfile.objects.create(
        user=user, university=University.objects.get_or_create(name="UDSM")[0], course="Computer Science",
        academic_year=3, region=slot.district.region, district=slot.district,
    )
    app = create_application(student, slot)
    payment = Payment.objects.create(student=student, application=app, amount=15000, method=PaymentMethod.M_PESA)
    finalize_verified_payment(payment, gateway_txn_id=f"GW-{email}")
    if not cooling_off:
        Application.objects.filter(pk=app.pk).update(can_cancel=False)
    return Application.objects.select_related("payment", "student__user", "slot__organization").get(pk=app.pk)


# ── dashboard ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_dashboard_shows_kpis_todo_and_recent_applicants(alpha, district):
    org, owner = alpha
    slot = _slot(org, district, "Network Intern", capacity=2)
    _paid(slot, "a@x.tz")
    _paid(slot, "b@x.tz")
    slots = dash.slots_with_counts(org)
    numbers = dash.kpis(slots)
    assert numbers == {
        "active_slots": 1, "total_slots": 1, "capacity": 2, "filled": 2, "fill_percent": 100,
        "paid": 2, "accepted": 0, "waiting": 2,
    }
    todo = dash.todo_items(org, slots)
    assert any("2 paid applicants are waiting" in item["text"] for item in todo)
    assert any("is full" in item["text"] for item in todo)

    page = _client(owner).get(reverse("organization-dashboard"))
    assert page.status_code == 200
    body = page.content.decode()
    assert "To do" in body and "Network Intern" in body and "Recent applicants" in body
    assert "Stu a@x" in body  # applicants are listed by name


@pytest.mark.django_db
def test_payment_notifies_every_member_who_may_review(alpha, district):
    org, owner = alpha
    recruiter = _verified(
        add_organization_member(org, "alpha-rec@o.tz", PW, role=OrganizationMemberRole.RECRUITER).user
    )
    slot = _slot(org, district)
    _paid(slot, "n@x.tz")
    for member in (owner, recruiter):
        note = Notification.objects.get(user=member, title="New Applicant")
        assert note.link == f"/organization/slots/{slot.id}/applicants/"


# ── slots: delete only without applications, duplicate ──────────────────────

@pytest.mark.django_db
def test_slot_with_applications_cannot_be_deleted(alpha, district):
    org, owner = alpha
    c = _client(owner)
    empty = _slot(org, district, "Empty slot")
    busy = _slot(org, district, "Busy slot")
    _paid(busy, "busy@x.tz")

    c.post(reverse("organization-slot-delete", args=[busy.id]))
    assert Slot.objects.filter(pk=busy.pk).exists()
    assert AuditLog.objects.filter(action="Slot Delete Refused", outcome="REJECTED").exists()

    c.post(reverse("organization-slot-delete", args=[empty.id]))
    assert not Slot.objects.filter(pk=empty.pk).exists()

    by_title = {s.title: s for s in dash.slots_with_counts(org)}
    assert by_title["Busy slot"].can_delete is False


@pytest.mark.django_db
def test_slot_and_applicant_pages_show_the_new_actions(alpha, district):
    org, owner = alpha
    c = _client(owner)
    empty = _slot(org, district, "Empty slot")
    busy = _slot(org, district, "Busy slot")
    app = _paid(busy, "busy@x.tz")

    slots_page = c.get(reverse("organization-slots")).content.decode()
    assert reverse("organization-slot-duplicate", args=[busy.id]) in slots_page
    assert reverse("organization-slot-delete", args=[empty.id]) in slots_page
    assert reverse("organization-slot-delete", args=[busy.id]) not in slots_page  # has applications

    applicants_page = c.get(reverse("organization-applicants", args=[busy.id]))
    assert applicants_page.status_code == 200
    assert reverse("organization-applicant-notes", args=[app.id]) in applicants_page.content.decode()

    accept_and_notify(app, company_message="See you Monday", actor=owner)
    letter = c.get(reverse("organization-applicant-acceptance-letter", args=[app.id]))
    assert letter.status_code == 200 and letter.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_duplicate_slot_prefills_the_new_slot_form(alpha, beta, district):
    org, owner = alpha
    slot = _slot(org, district, "Lab Assistant")
    page = _client(owner).get(reverse("organization-slot-duplicate", args=[slot.id]))
    assert page.status_code == 200
    assert page.context["form"].initial["title"] == "Lab Assistant"
    assert "application_deadline" not in page.context["form"].initial
    assert page.context["form_action"] == reverse("organization-slot-create")
    # Another organization cannot copy our slot.
    assert _client(beta[1]).get(reverse("organization-slot-duplicate", args=[slot.id])).status_code == 404


# ── accepting ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_accept_notifies_the_student_in_app_and_respects_the_cancel_window(alpha, district):
    org, owner = alpha
    slot = _slot(org, district)
    cooling = _paid(slot, "cool@x.tz", cooling_off=True)
    with pytest.raises(AcceptanceError):
        accept_and_notify(cooling, actor=owner)

    ready = _paid(slot, "ready@x.tz")
    resp = _client(owner).post(
        reverse("organization-acceptance-sms", args=[ready.id]),
        {"message": "Report Monday 8 AM", "next": reverse("organization-dashboard")},
    )
    assert resp.status_code == 302 and resp.url == reverse("organization-dashboard")
    ready.refresh_from_db()
    assert ready.is_accepted and ready.company_message == "Report Monday 8 AM"
    note = Notification.objects.get(user=ready.student.user, type=NotificationType.ACCEPTANCE)
    assert note.link == f"/student/applications/{ready.id}/next-steps/"
    assert AuditLog.objects.filter(action="Application Accepted", organization=org).count() == 1


def test_there_is_no_way_to_reject_a_paid_application():
    """A paid student only waits for acceptance: no rejected status, no reject URL."""
    from django.urls import get_resolver

    assert "REJECTED" not in ApplicationStatus.values
    names = [name for name in get_resolver().reverse_dict.keys() if isinstance(name, str)]
    assert not [n for n in names if "reject" in n.lower() and "organization" in n.lower()]


@pytest.mark.django_db
def test_bulk_accept_uses_template_skips_ineligible_and_is_tenant_scoped(alpha, beta, district):
    org, owner = alpha
    beta_org, beta_owner = beta
    slot = _slot(org, district, capacity=5)
    one, two = _paid(slot, "one@x.tz"), _paid(slot, "two@x.tz")
    cooling = _paid(slot, "three@x.tz", cooling_off=True)
    foreign = _paid(_slot(beta_org, district, "Beta slot"), "foreign@x.tz")
    template = AcceptanceTemplate.objects.create(organization=org, name="Jan", body="Report on 6 January.")

    resp = _client(owner).post(
        reverse("organization-bulk-accept"),
        {"application": [str(one.id), str(two.id), str(cooling.id), str(foreign.id)], "template": str(template.id)},
    )
    assert resp.status_code == 302
    for app in (one, two):
        app.refresh_from_db()
        assert app.is_accepted and app.company_message == "Report on 6 January."
    cooling.refresh_from_db()
    foreign.refresh_from_db()
    assert not cooling.is_accepted and not foreign.is_accepted

    # A recruiter may view applicants but not accept.
    recruiter = _verified(
        add_organization_member(org, "alpha-rec@o.tz", PW, role=OrganizationMemberRole.RECRUITER).user
    )
    assert _client(recruiter).post(reverse("organization-bulk-accept"), {"application": [str(cooling.id)]}).status_code == 403
    assert _client(recruiter).get(reverse("organization-all-applicants")).status_code == 200


# ── applicants list, notes, export ──────────────────────────────────────────

@pytest.mark.django_db
def test_all_applicants_lists_only_own_and_filters(alpha, beta, district):
    org, owner = alpha
    beta_org, beta_owner = beta
    slot = _slot(org, district)
    mine = _paid(slot, "mine@x.tz")
    accept_and_notify(_paid(slot, "done@x.tz"), actor=owner)
    _paid(_slot(beta_org, district, "Beta slot"), "theirs@x.tz")
    c = _client(owner)

    page = c.get(reverse("organization-all-applicants"))
    assert page.status_code == 200 and page.context["total"] == 2
    waiting = c.get(reverse("organization-all-applicants"), {"status": "waiting"})
    assert [a.id for a in waiting.context["applications"]] == [mine.id]
    # A slot id of another organization shows nothing, never everything.
    foreign_slot = Slot.objects.get(title="Beta slot")
    assert c.get(reverse("organization-all-applicants"), {"slot": str(foreign_slot.id)}).context["total"] == 0


@pytest.mark.django_db
def test_private_notes_are_tenant_isolated_and_audited(alpha, beta, district):
    org, owner = alpha
    app = _paid(_slot(org, district), "noted@x.tz")
    url = reverse("organization-applicant-notes", args=[app.id])
    c = _client(owner)
    assert c.post(url, {"body": "Strong in networking."}).status_code == 302
    note = ApplicantNote.objects.get(application=app)
    assert note.organization_id == org.id and note.author_id == owner.id
    assert "Strong in networking." in c.get(url).content.decode()
    assert AuditLog.objects.filter(action="Applicant Note Added", organization=org).exists()

    other = _client(beta[1])
    assert other.get(url).status_code == 404
    assert other.post(url, {"body": "x"}).status_code == 404
    assert other.post(url, {"action": "delete", "note_id": str(note.id)}).status_code == 404
    assert ApplicantNote.objects.count() == 1

    # The student never sees the note.
    student_page = _client(app.student.user).get(reverse("student-applications"))
    assert "Strong in networking." not in student_page.content.decode()


@pytest.mark.django_db
def test_export_contains_only_own_applicants_and_is_audited(alpha, beta, district):
    org, owner = alpha
    _paid(_slot(org, district, "Our slot"), "ours@x.tz")
    _paid(_slot(beta[0], district, "Their slot"), "theirs@x.tz")
    resp = _client(owner).get(reverse("organization-export-applicants"))
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    text = resp.content.decode("utf-8-sig")
    assert "ours@x.tz" in text and "theirs@x.tz" not in text and "Our slot" in text
    event = AuditLog.objects.get(action="Applicants Exported")
    assert event.organization_id == org.id and event.context["rows"] == 1


@pytest.mark.django_db
def test_acceptance_templates_crud_and_isolation(alpha, beta):
    org, owner = alpha
    c = _client(owner)
    url = reverse("organization-templates")
    c.post(url, {"name": "January", "body": "Report on 6 January."})
    template = AcceptanceTemplate.objects.get(organization=org)
    c.post(url, {"name": "January", "body": "Duplicate name"})
    assert AcceptanceTemplate.objects.filter(organization=org).count() == 1
    c.post(url, {"template_id": str(template.id), "name": "Jan intake", "body": "Report on 7 January."})
    template.refresh_from_db()
    assert template.name == "Jan intake" and template.body == "Report on 7 January."
    # Another organization can neither see nor delete it.
    other = _client(beta[1])
    assert "Jan intake" not in other.get(url).content.decode()
    assert other.post(url, {"template_id": str(template.id), "action": "delete"}).status_code == 404
    c.post(url, {"template_id": str(template.id), "action": "delete"})
    assert not AcceptanceTemplate.objects.exists()


# ── insights, activity, ratings ─────────────────────────────────────────────

@pytest.mark.django_db
def test_insights_activity_and_reviews_pages(alpha, beta, district):
    org, owner = alpha
    slot = _slot(org, district)
    app = _paid(slot, "rated@x.tz")
    accept_and_notify(app, actor=owner)
    OrganizationReview.objects.create(application=app, organization=org, student=app.student, rating=4, comment="Great mentors")
    c = _client(owner)

    insights = c.get(reverse("organization-insights"))
    assert insights.status_code == 200
    charts = {chart["id"]: chart for chart in insights.context["charts"]}
    assert sum(charts["applications_per_week"]["datasets"][0]["data"]) == 1
    assert charts["acceptance_status"]["data"] == [1, 0]
    assert charts["by_university"]["items"][0] == {"label": "UDSM", "values": [1], "region": ""}

    activity = c.get(reverse("organization-activity"))
    assert activity.status_code == 200 and "Application Accepted" in activity.content.decode()

    reviews = c.get(reverse("organization-reviews"))
    assert reviews.status_code == 200 and reviews.context["average"] == 4.0
    assert "Great mentors" in reviews.content.decode()
    # Beta sees none of Alpha's data.
    assert _client(beta[1]).get(reverse("organization-reviews")).context["review_total"] == 0
    assert "Application Accepted" not in _client(beta[1]).get(reverse("organization-activity")).content.decode()

    # A recruiter has no audit permission, so no team activity page.
    recruiter = _verified(
        add_organization_member(org, "alpha-rec@o.tz", PW, role=OrganizationMemberRole.RECRUITER).user
    )
    assert _client(recruiter).get(reverse("organization-activity")).status_code == 403
