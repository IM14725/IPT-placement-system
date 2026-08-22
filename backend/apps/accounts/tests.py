import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from apps.accounts.models import UserRole

User = get_user_model()


@pytest.mark.django_db
def test_superuser_role_is_admin():
    user = User.objects.create_superuser(email="admin@x.tz", password="Secret123!")
    assert user.is_superuser
    assert user.role == UserRole.ADMIN
    assert user.is_platform_admin


@pytest.mark.django_db
def test_role_helpers():
    student = User.objects.create_user(email="s@x.tz", password="Secret123!", role=UserRole.STUDENT)
    company = User.objects.create_user(email="c@x.tz", password="Secret123!", role=UserRole.COMPANY)
    assert student.is_student and not student.is_company
    assert company.is_company and not company.is_student


@pytest.mark.django_db
def test_email_is_username_field():
    user = User.objects.create_user(email="unique@x.tz", password="Secret123!")
    assert user.USERNAME_FIELD == "email"
    assert user.email == "unique@x.tz"


@pytest.mark.django_db
def test_drf_token_issued():
    user = User.objects.create_user(email="tok@x.tz", password="Secret123!")
    token = Token.objects.create(user=user)
    assert token.key
    assert token.user == user