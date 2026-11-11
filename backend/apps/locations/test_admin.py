import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import AdminRole, User, UserRole
from apps.core.models import AuditLog
from apps.locations.models import District, Region, Ward
from apps.slots.models import Slot

PW = "Secret123!x"
URL = "platform-locations"


@pytest.fixture
def admin_client(db):
    admin = User.objects.create_user(email="admin@x.tz", password=PW, role=UserRole.SUPER_ADMIN, is_staff=True)
    client = Client()
    client.force_login(admin)
    return client


def _client_for(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def region(db):
    return Region.objects.create(name="Arusha", slug="arusha")


@pytest.fixture
def district(region):
    return District.objects.create(name="Meru", region=region)


def _post(client, **data):
    return client.post(reverse(URL), data)


def test_student_cannot_open_locations(db):
    user = User.objects.create_user(email="s@x.tz", password=PW, role=UserRole.STUDENT)
    client = _client_for(user)
    assert client.get(reverse(URL)).status_code == 403
    assert _post(client, kind="regions", action="create", name="Hack").status_code == 403
    assert not Region.objects.filter(name="Hack").exists()


def test_platform_admin_needs_locations_module(db):
    role = AdminRole.objects.create(name="Support", permissions={"user_management": {"view": True}})
    admin = User.objects.create_user(email="a@x.tz", password=PW, role=UserRole.ADMIN, is_staff=True, admin_role=role)
    assert _client_for(admin).get(reverse(URL)).status_code == 403

    role.permissions = {"locations": {"view": True}}
    role.save()
    admin = User.objects.get(pk=admin.pk)
    client = _client_for(admin)
    assert client.get(reverse(URL)).status_code == 200
    # view-only: cannot create
    assert _post(client, kind="regions", action="create", name="Tanga").status_code == 403


def test_admin_adds_region_with_slug_and_audit(admin_client):
    resp = _post(admin_client, kind="regions", action="create", name="  Dar  es Salaam ")
    assert resp.status_code == 302
    region = Region.objects.get(name="Dar es Salaam")
    assert region.slug == "dar-es-salaam"
    assert AuditLog.objects.filter(action="Region Added").exists()


def test_duplicate_region_rejected(admin_client, region):
    resp = _post(admin_client, kind="regions", action="create", name="arusha")
    assert resp.status_code == 200
    assert Region.objects.count() == 1


def test_admin_edits_region(admin_client, region):
    _post(admin_client, kind="regions", action="update", id=str(region.pk), name="Arusha City")
    region.refresh_from_db()
    assert (region.name, region.slug) == ("Arusha City", "arusha-city")


def test_district_unique_within_region(admin_client, region, district):
    resp = _post(admin_client, kind="districts", action="create", name="meru", region=str(region.pk))
    assert resp.status_code == 200
    assert District.objects.count() == 1


def test_admin_adds_ward(admin_client, district):
    resp = _post(admin_client, kind="wards", action="create", name="Kikatiti", district=str(district.pk))
    assert resp.status_code == 302
    assert Ward.objects.filter(name="Kikatiti", district=district).exists()


def test_delete_unused_ward(admin_client, district):
    ward = Ward.objects.create(name="Temp", district=district)
    _post(admin_client, kind="wards", action="delete", id=str(ward.pk))
    assert not Ward.objects.filter(pk=ward.pk).exists()
    assert AuditLog.objects.filter(action="Ward Deleted").exists()


def test_delete_district_blocked_by_slot(admin_client, district):
    from apps.companies.models import Organization
    from apps.students.models import VerificationStatus

    owner = User.objects.create_user(email="o@x.tz", password=PW, role=UserRole.ORGANIZATION)
    org = Organization.objects.create(
        user=owner, name="Acme", industry="Tech", verification_status=VerificationStatus.APPROVED
    )
    Slot.objects.create(organization=org, title="Dev", industry="Tech", role_type="Intern", district=district)

    _post(admin_client, kind="districts", action="delete", id=str(district.pk))
    assert District.objects.filter(pk=district.pk).exists()
    assert Slot.objects.count() == 1


def test_delete_region_blocked_by_districts(admin_client, region, district):
    _post(admin_client, kind="regions", action="delete", id=str(region.pk))
    assert Region.objects.filter(pk=region.pk).exists()


def test_ward_edit_invalidates_cached_ward_list(db, district, monkeypatch):
    from apps.core import cache

    deleted = []
    monkeypatch.setattr(cache, "cache_delete", deleted.append)
    Ward.objects.create(name="Kikatiti", district=district)
    assert f"locations:wards:{district.pk}" in deleted
