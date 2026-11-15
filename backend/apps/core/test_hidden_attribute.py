"""Tabs and toggles hide panels with the HTML ``hidden`` attribute. Tailwind display classes (flex, grid)
would otherwise override it and leave a "hidden" panel on screen, so base.html must keep this rule."""

import pytest
from django.test import Client


@pytest.mark.django_db
def test_base_template_forces_the_hidden_attribute_to_win():
    body = Client().get("/").content.decode()
    assert "[hidden]{display:none !important}" in body


@pytest.mark.django_db
def test_profile_tabs_markup_has_matching_tabs_and_panels(client):
    from apps.accounts.models import User, UserRole
    from apps.students.models import StudentProfile

    user = User.objects.create_user(
        email="tabs@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True
    )
    StudentProfile.objects.create(user=user, course="CS")
    client.force_login(user)
    body = client.get("/student/profile/").content.decode()
    for name in ("profile", "security"):
        assert f'data-tab="{name}"' in body and f'data-panel="{name}"' in body
    assert "js/tabs.js" in body
