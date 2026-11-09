import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client

from apps.accounts.models import UserRole
from apps.companies.models import (
    MAX_ORGANIZATION_ACCOUNTS,
    MAX_ORGANIZATION_CONCURRENT_LOGINS,
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    OrganizationSession,
    OrganizationType,
)
from apps.companies.services import (
    add_organization_member,
    can_organization_login,
    create_organization,
    get_user_organization,
)

User = get_user_model()


@pytest.mark.django_db
def test_unified_organization_and_up_to_five_member_accounts():
    owner = User.objects.create_user(
        email="owner@org.tz", password="Secret123!", role=UserRole.ORGANIZATION
    )
    org = create_organization(
        user=owner,
        name="Tanzania Tech Hub",
        organization_type=OrganizationType.COMPANY,
    )
    assert org.organization_type == OrganizationType.COMPANY
    assert OrganizationMember.objects.filter(organization=org).count() == 1

    # Add 4 more accounts (total 5)
    members = []
    for i in range(2, MAX_ORGANIZATION_ACCOUNTS + 1):
        m = add_organization_member(
            org,
            email=f"member{i}@org.tz",
            password="Secret123!",
            role=OrganizationMemberRole.MANAGER if i == 2 else OrganizationMemberRole.RECRUITER,
        )
        members.append(m)
        assert get_user_organization(m.user) == org

    assert OrganizationMember.objects.filter(organization=org).count() == MAX_ORGANIZATION_ACCOUNTS

    # 6th account should be rejected
    with pytest.raises(ValidationError):
        add_organization_member(
            org,
            email="member6@org.tz",
            password="Secret123!",
            role=OrganizationMemberRole.RECRUITER,
        )


@pytest.mark.django_db
def test_up_to_five_concurrent_organization_logins_at_once():
    owner = User.objects.create_user(
        email="owner@concurrent.tz", password="Secret123!", role=UserRole.ORGANIZATION
    )
    org = create_organization(user=owner, name="Concurrent Org Ltd")
    users = [owner]
    for i in range(2, MAX_ORGANIZATION_ACCOUNTS + 1):
        m = add_organization_member(
            org,
            email=f"staff{i}@concurrent.tz",
            password="Secret123!",
            role=OrganizationMemberRole.RECRUITER,
        )
        users.append(m.user)

    clients = []
    for u in users:
        c = Client()
        logged_in = c.login(email=u.email, password="Secret123!")
        assert logged_in is True
        clients.append(c)

    assert OrganizationSession.objects.filter(organization=org).count() == MAX_ORGANIZATION_CONCURRENT_LOGINS

    # The cap counts people, not devices: each person has one session, so the owner signing
    # in again (a second device) replaces their own session and is never blocked by it.
    ok, _ = can_organization_login(owner, session_key="owner-second-device")
    assert ok is True

    # Logging out one client frees a slot immediately
    clients[0].logout()
    assert OrganizationSession.objects.filter(organization=org).count() == MAX_ORGANIZATION_CONCURRENT_LOGINS - 1
