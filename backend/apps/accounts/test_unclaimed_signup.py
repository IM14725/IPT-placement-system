"""Someone who registers and stops at the verification-code page must be able to carry on."""
import re

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import mail, signing
from django.test import Client

from apps.accounts.models import UserRole
from apps.accounts.tasks import process_registration_task
from apps.accounts.unclaimed import is_unclaimed, unclaimed_users

User = get_user_model()
FIRST, SECOND = "Secret123!x", "Another456!yy"


def _form(email="stuck@x.tz", password=FIRST, **extra):
    data = {
        "email": email, "role": "STUDENT", "first_name": "Stu", "last_name": "Ck",
        "phone": "", "password_hash": make_password(password),
    }
    data.update(extra)
    return data


def _code():
    return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)


def _finalize(client, user_id):
    token = signing.TimestampSigner(salt="accounts.finalize-auth").sign(str(user_id))
    session = client.session
    session["auth_finalize_token"] = token
    session.save()
    return client.post("/auth/finalize/", {"token": token})


@pytest.mark.django_db
def test_registering_again_resumes_instead_of_a_dead_end():
    first = process_registration_task(_form())
    user = User.objects.get(email="stuck@x.tz")
    assert first["user_id"] == str(user.id) and not user.email_verified and is_unclaimed(user)

    mail.outbox.clear()
    again = process_registration_task(_form(password=SECOND))  # a new browser, a different password typed
    assert again == {"status": "success", "user_id": str(user.id), "resumed": True}
    assert User.objects.filter(email="stuck@x.tz").count() == 1
    user.refresh_from_db()
    assert user.check_password(FIRST) and not user.check_password(SECOND)  # nothing is overwritten
    assert not [m for m in mail.outbox if "tried to register" in m.subject]  # no scary "someone tried" email

    client = Client()
    resp = _finalize(client, again["user_id"])
    assert resp.status_code == 302 and resp.url.endswith("/auth/verify-email/")
    assert mail.outbox[-1].to == ["stuck@x.tz"]  # a real code went to the mailbox
    ok = client.post("/auth/verify-email/", {"code": _code()})
    assert ok.status_code == 302 and client.session["_auth_user_id"] == str(user.id)
    user.refresh_from_db()
    assert user.email_verified and not is_unclaimed(user)


@pytest.mark.django_db
def test_an_account_that_was_verified_still_gets_the_neutral_outcome():
    process_registration_task(_form())
    User.objects.filter(email="stuck@x.tz").update(email_verified=True)
    mail.outbox.clear()
    result = process_registration_task(_form(password=SECOND))
    assert result["user_id"] == "decoy" and "resumed" not in result
    assert [m for m in mail.outbox if "tried to register" in m.subject]


@pytest.mark.django_db
def test_login_with_the_first_password_continues_the_verification():
    process_registration_task(_form())
    client = Client()
    resp = client.post("/auth/login/", {"email": "stuck@x.tz", "password": FIRST})
    assert resp.status_code == 302 and resp.url.endswith("/auth/verify-email/")
    assert client.post("/auth/verify-email/", {"code": _code()}).status_code == 302
    assert client.session["_auth_user_id"]


@pytest.mark.django_db
def test_the_code_page_without_a_session_says_what_to_do(client):
    resp = client.get("/auth/verify-email/", follow=True)
    assert resp.redirect_chain[-1][0].endswith("/auth/login/")
    assert "new verification code" in resp.content.decode()


@pytest.mark.django_db
def test_forgotten_password_works_for_an_unverified_account_and_verifies_the_email():
    process_registration_task(_form())
    user = User.objects.get(email="stuck@x.tz")
    client = Client()
    assert client.post("/auth/forgot-password/", {"email": "stuck@x.tz"}).status_code == 302
    mail.outbox.clear()
    assert client.post("/auth/reset-password/method/", {"channel": "EMAIL"}).status_code == 302
    assert mail.outbox and mail.outbox[-1].to == ["stuck@x.tz"]
    assert client.post("/auth/reset-password/verify/", {"code": _code()}).url.endswith("/auth/reset-password/")
    resp = client.post("/auth/reset-password/", {"password1": SECOND, "password2": SECOND})
    assert resp.status_code == 302
    user.refresh_from_db()
    assert user.check_password(SECOND) and user.email_verified


@pytest.mark.django_db
def test_an_sms_code_still_needs_a_verified_phone():
    process_registration_task(_form(phone="+255712345678"))
    client = Client()
    client.post("/auth/forgot-password/", {"email": "stuck@x.tz"})
    mail.outbox.clear()
    client.post("/auth/reset-password/method/", {"channel": "SMS"})
    from apps.accounts.models import OneTimeCode

    assert not OneTimeCode.objects.filter(channel="SMS").exists()  # unverified phone: nothing sent


@pytest.mark.django_db
def test_a_mistyped_first_attempt_does_not_block_the_correct_phone():
    process_registration_task(_form(email="typo@x.tz", phone="+255712345678"))
    typo = User.objects.get(email="typo@x.tz")
    # the form no longer calls the number "taken" because the account holding it never proved anything
    from apps.accounts.forms import RegisterForm

    form = RegisterForm(
        data={"email": "right@x.tz", "role": "STUDENT", "first_name": "A", "last_name": "B",
              "phone": "+255712345678", "password1": FIRST, "password2": FIRST, "accept_terms": "on"}
    )
    form.is_valid()
    assert "phone" not in form.errors
    result = process_registration_task(_form(email="right@x.tz", phone="+255712345678"))
    assert result["status"] == "success" and result["user_id"] != str(typo.id)
    assert not User.objects.filter(email="typo@x.tz").exists()  # the abandoned one was released
    assert User.objects.get(email="right@x.tz").phone == "255712345678"


@pytest.mark.django_db
def test_a_verified_accounts_phone_and_an_invited_members_account_are_never_released():
    process_registration_task(_form(email="owner@x.tz", phone="+255712345678"))
    User.objects.filter(email="owner@x.tz").update(email_verified=True)
    result = process_registration_task(_form(email="other@x.tz", phone="+255712345678"))
    assert result["status"] == "error"  # taken by a real account
    assert User.objects.filter(email="owner@x.tz").exists()

    invited = User.objects.create_user(
        email="invited@x.tz", password=FIRST, role=UserRole.ORGANIZATION, must_change_password=True
    )
    assert not is_unclaimed(invited) and invited.pk not in unclaimed_users().values_list("pk", flat=True)


@pytest.mark.django_db
def test_an_abandoned_organization_name_can_be_registered_again():
    org = {"role": "ORGANIZATION", "organization_name": "Acme Ltd", "organization_email": "info@acme.tz",
           "organization_phone": "+255700000001", "position": "Manager", "phone": ""}
    assert process_registration_task(_form(email="a1@x.tz", **org))["status"] == "success"
    from apps.companies.services import organization_email_taken, organization_name_taken

    assert not organization_name_taken("Acme Ltd") and not organization_email_taken("info@acme.tz")
    assert process_registration_task(_form(email="a2@x.tz", **org))["status"] == "success"
    assert not User.objects.filter(email="a1@x.tz").exists() and User.objects.filter(email="a2@x.tz").exists()
