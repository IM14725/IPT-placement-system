from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.applications.models import Application, ApplicationStatus
from apps.applications.services import SlotFullError, create_application
from apps.companies.forms import SlotForm
from apps.companies.models import Organization
from apps.locations.models import District, Region
from apps.slots.models import Slot
from apps.slots.views import _load_slot_search
from apps.students.models import StudentProfile, VerificationStatus

PW = "Secret123!x"


@pytest.fixture
def district(db):
    region = Region.objects.create(name="Dar es Salaam", slug="dar-es-salaam")
    return District.objects.create(name="Kinondoni", region=region)


def make_org(email, name, **extra):
    user = User.objects.create_user(email=email, password=PW, role=UserRole.ORGANIZATION)
    return Organization.objects.create(
        user=user, name=name, industry="Tech", verification_status=VerificationStatus.APPROVED, **extra
    )


def make_slot(org, district, **extra):
    return Slot.objects.create(
        organization=org, title="Intern", industry="Tech", role_type="Internship",
        district=district, capacity=3, **extra,
    )


def make_student(email):
    user = User.objects.create_user(email=email, password=PW, role=UserRole.STUDENT)
    return StudentProfile.objects.create(
        user=user, course="CS", academic_year=3, verification_status=VerificationStatus.APPROVED
    )


def test_deadline_passed_property(district):
    org = make_org("o@x.tz", "Acme")
    assert not make_slot(org, district).deadline_passed
    assert not make_slot(org, district, application_deadline=timezone.now() + timedelta(days=1)).deadline_passed
    assert make_slot(org, district, application_deadline=timezone.now() - timedelta(minutes=1)).deadline_passed


def test_cannot_start_application_after_deadline(district):
    org = make_org("o@x.tz", "Acme")
    slot = make_slot(org, district, application_deadline=timezone.now() - timedelta(hours=1))
    with pytest.raises(SlotFullError, match="deadline"):
        create_application(make_student("s@x.tz"), slot)
    assert not Application.objects.exists()


def test_existing_reservation_can_still_be_paid_after_deadline(district):
    org = make_org("o@x.tz", "Acme")
    slot = make_slot(org, district, application_deadline=timezone.now() + timedelta(hours=1))
    student = make_student("s@x.tz")
    application = create_application(student, slot)
    assert application.status == ApplicationStatus.PENDING

    Slot.objects.filter(pk=slot.pk).update(application_deadline=timezone.now() - timedelta(minutes=5))
    slot.refresh_from_db()
    assert slot.deadline_passed
    assert create_application(student, slot).pk == application.pk


def test_no_deadline_means_always_open(district):
    org = make_org("o@x.tz", "Acme")
    assert create_application(make_student("s@x.tz"), make_slot(org, district)).status == ApplicationStatus.PENDING


def test_suspended_organization_not_accepting_applications(district):
    org = make_org("o@x.tz", "Acme", suspended_at=timezone.now())
    with pytest.raises(SlotFullError, match="not accepting"):
        create_application(make_student("s@x.tz"), make_slot(org, district))


def test_slot_form_rejects_past_deadline(district):
    data = {
        "title": "Intern", "role_type": "Internship", "district": district.pk, "capacity": 1,
        "application_deadline": (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
    }
    form = SlotForm({**data, "region": district.region_id})
    assert not form.is_valid()
    assert "application_deadline" in form.errors

    future = (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
    form = SlotForm({**data, "region": district.region_id, "application_deadline": future})
    assert form.is_valid(), form.errors


def test_search_filters_by_organization_name(district):
    make_slot(make_org("a@x.tz", "Alpha Logistics"), district)
    make_slot(make_org("b@x.tz", "Beta Bank"), district)
    names = {s["organization_name"] for s in _load_slot_search(None, None, None, None, None, "alpha")}
    assert names == {"Alpha Logistics"}
    assert len(_load_slot_search(None, None, None, None, None, "")) == 2


def test_search_hides_suspended_organizations(district):
    make_slot(make_org("a@x.tz", "Alpha Logistics"), district)
    make_slot(make_org("b@x.tz", "Beta Bank", suspended_at=timezone.now()), district)
    names = {s["organization_name"] for s in _load_slot_search(None, None, None, None, None)}
    assert names == {"Alpha Logistics"}


def test_search_exposes_deadline_fields(district):
    make_slot(
        make_org("a@x.tz", "Alpha"), district,
        application_deadline=timezone.now() - timedelta(days=1),
    )
    row = _load_slot_search(None, None, None, None, None)[0]
    assert row["deadline_passed"] is True
    assert row["application_deadline"]
