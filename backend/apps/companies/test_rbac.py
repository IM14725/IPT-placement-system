import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.accounts.models import AdminRole, UserRole
from apps.companies.models import OrganizationMemberRole
from apps.companies.permissions import (
    APPLICANTS_ACCEPT,
    MEMBERS_MANAGE,
    PROFILE_EDIT,
    SLOTS_MANAGE,
    org_has_perm,
)
from apps.companies.services import add_organization_member, create_organization

User = get_user_model()
PW = "Secret123!x"


@pytest.fixture
def org_users(db):
    owner = User.objects.create_user(email="owner@o.tz", password=PW, role=UserRole.ORGANIZATION)
    org = create_organization(user=owner, name="RBAC Org")
    manager = add_organization_member(
        org, "manager@o.tz", PW, role=OrganizationMemberRole.MANAGER
    ).user
    recruiter = add_organization_member(
        org, "recruiter@o.tz", PW, role=OrganizationMemberRole.RECRUITER
    ).user
    # These tests are about role permissions, not the first-login temporary-password step.
    User.objects.filter(pk__in=[manager.pk, recruiter.pk]).update(must_change_password=False)
    return org, owner, manager, recruiter


_clients = {}


def _client(user):
    """One logged-in client per user (orgs cap concurrent sessions at 5)."""
    c = _clients.get(user.pk)
    if c is None:
        c = _clients[user.pk] = Client()
        c.force_login(user)
    return c


@pytest.fixture(autouse=True)
def _reset_clients():
    _clients.clear()
    yield
    _clients.clear()


@pytest.mark.django_db
def test_role_permission_matrix(org_users):
    org, owner, manager, recruiter = org_users
    assert org_has_perm(owner, MEMBERS_MANAGE, org)
    assert not org_has_perm(manager, MEMBERS_MANAGE, org)
    assert org_has_perm(manager, PROFILE_EDIT, org)
    assert org_has_perm(manager, APPLICANTS_ACCEPT, org)
    assert not org_has_perm(recruiter, PROFILE_EDIT, org)
    assert not org_has_perm(recruiter, APPLICANTS_ACCEPT, org)
    assert org_has_perm(recruiter, SLOTS_MANAGE, org)


@pytest.mark.django_db
def test_staff_accounts_reach_org_pages_but_are_role_limited(org_users):
    org, owner, manager, recruiter = org_users
    profile = reverse("organization-profile")
    # Members (not just the owner) can open the organization pages.
    for u in (owner, manager, recruiter):
        assert _client(u).get(profile).status_code == 200

    # Profile edits: recruiter denied, manager allowed.
    assert _client(recruiter).post(profile, {"name": "X"}).status_code == 403
    assert _client(manager).post(profile, {"name": "RBAC Org"}).status_code != 403

    add = reverse("organization-add-member")
    data = {"email": "n@o.tz", "role": "RECRUITER", "password": PW}
    assert _client(recruiter).post(add, data).status_code == 403
    assert _client(manager).post(add, data).status_code == 403
    assert _client(owner).post(add, data).status_code == 302


@pytest.mark.django_db
def test_recruiter_cannot_accept_but_can_reach_slots(org_users):
    org, owner, manager, recruiter = org_users
    accept = reverse("organization-acceptance-sms", args=["00000000-0000-0000-0000-000000000000"])
    assert _client(recruiter).post(accept).status_code == 403
    # Slots are allowed for the role; the unapproved org redirects to its profile.
    assert _client(recruiter).get(reverse("organization-slots")).status_code == 302


@pytest.mark.django_db
def test_student_cannot_use_org_views():
    s = User.objects.create_user(email="s@x.tz", password=PW, role=UserRole.STUDENT)
    assert _client(s).get(reverse("organization-profile")).status_code == 403


@pytest.mark.django_db
def test_admin_roles_and_console_gate():
    sup = User.objects.create_superuser(email="root@x.tz", password=PW)
    assert sup.role == UserRole.SUPER_ADMIN and sup.is_super_admin and sup.is_platform_admin

    role = AdminRole.objects.create(
        name="Finance",
        permissions={"financial_ledger": {"view": True}},
    )
    admin = User.objects.create_user(
        email="fin@x.tz", password=PW, role=UserRole.ADMIN, admin_role=role
    )
    bare = User.objects.create_user(email="bare@x.tz", password=PW, role=UserRole.ADMIN)

    assert admin.has_admin_permission("financial_ledger", "view")
    assert not admin.has_admin_permission("financial_ledger", "edit")
    assert not admin.has_admin_permission("user_management", "view")
    assert not bare.has_admin_permission("financial_ledger", "view")
    assert sup.has_admin_permission("user_management", "delete")

    assert _client(admin).get(reverse("platform-ledger")).status_code == 200
    assert _client(admin).get(reverse("platform-metrics")).status_code == 403
    assert _client(bare).get(reverse("platform-ledger")).status_code == 403
    # Role management is Super Admin only.
    assert _client(admin).get(reverse("platform-roles")).status_code == 403
    assert _client(sup).get(reverse("platform-roles")).status_code == 200
