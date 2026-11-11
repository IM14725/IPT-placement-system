"""Tier 2 hardening of register/login: no account enumeration, leaked-password check, keyless bot
check, and the optional organization identifiers (BRELA / TIN)."""

import time

import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.signing import TimestampSigner

from apps.accounts import antibot
from apps.accounts.forms import LoginForm, RegisterForm
from apps.accounts.models import User, UserRole
from apps.accounts.tasks import DECOY_USER_ID, process_registration_task
from apps.accounts.validators import _breached
from apps.accounts.views import (
    AUTH_FINALIZE_SALT,
    AUTH_FINALIZE_SESSION_KEY,
    DECOY_EMAIL_SESSION_KEY,
)
from apps.accounts.views_otp import VERIFY_USER_KEY
from apps.companies.identifiers import is_free_email, normalize_brela, normalize_tin
from apps.companies.models import Organization

STRONG = "Zx9!kQ4#vTe2"


def _form_data(**over):
    data = {
        "email": "new@example.tz",
        "password_hash": "pbkdf2_sha256$1$x$y",
        "role": "STUDENT",
        "first_name": "Asha",
        "last_name": "Juma",
        "phone": "",
        "login_url": "http://testserver/auth/login/",
        "reset_url": "http://testserver/auth/forgot-password/",
    }
    data.update(over)
    return data


# ------------------------------------------------------------------ #9 no account enumeration
@pytest.mark.django_db
def test_existing_email_gets_a_decoy_result_and_the_owner_is_told(mailoutbox):
    User.objects.create_user(email="owner@example.tz", password=STRONG, role=UserRole.STUDENT)
    result = process_registration_task(_form_data(email="owner@example.tz"))
    assert result == {"status": "success", "user_id": DECOY_USER_ID}
    assert User.objects.filter(email="owner@example.tz").count() == 1
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == ["owner@example.tz"]
    assert "already have an account" in mailoutbox[0].body
    assert "http://testserver/auth/login/" in mailoutbox[0].body


@pytest.mark.django_db
def test_owner_is_not_spammed_by_repeated_attempts(mailoutbox):
    User.objects.create_user(email="spam@example.tz", password=STRONG, role=UserRole.STUDENT)
    for _ in range(4):
        process_registration_task(_form_data(email="spam@example.tz"))
    assert len(mailoutbox) == 1


@pytest.mark.django_db
def test_register_form_does_not_reveal_an_existing_email():
    User.objects.create_user(email="taken@example.tz", password=STRONG, role=UserRole.STUDENT)
    form = RegisterForm(
        {
            "role": "STUDENT", "email": "taken@example.tz", "first_name": "A", "last_name": "B",
            "password1": STRONG, "password2": STRONG,
        }
    )
    assert "email" not in form.errors


@pytest.mark.django_db
def test_duplicate_phone_message_does_not_say_why():
    User.objects.create_user(email="p@example.tz", password=STRONG, role=UserRole.STUDENT, phone="255712345678")
    form = RegisterForm(
        {
            "role": "STUDENT", "email": "q@example.tz", "first_name": "A", "last_name": "B", "phone": "255712345678",
            "password1": STRONG, "password2": STRONG,
        }
    )
    message = str(form.errors["phone"][0])
    assert "can't be used" in message
    assert "already" not in message and "account" not in message


@pytest.mark.django_db
def test_phone_race_in_the_task_returns_the_generic_message():
    User.objects.create_user(email="p2@example.tz", password=STRONG, role=UserRole.STUDENT, phone="255755000111")
    result = process_registration_task(_form_data(email="fresh@example.tz", phone="255755000111"))
    assert result["status"] == "error"
    assert "can't be used" in result["message"] and "already" not in result["message"]


@pytest.mark.django_db
def test_decoy_finalize_leads_to_the_code_page_and_no_code_ever_works(client):
    User.objects.create_user(email="victim@example.tz", password=STRONG, role=UserRole.STUDENT)
    token = TimestampSigner(salt=AUTH_FINALIZE_SALT).sign(DECOY_USER_ID)
    session = client.session
    session[AUTH_FINALIZE_SESSION_KEY] = token
    session[DECOY_EMAIL_SESSION_KEY] = "victim@example.tz"
    session.save()

    response = client.post("/auth/finalize/", {"token": token})
    assert response.status_code == 302 and response.url.endswith("/auth/verify-email/")
    assert client.session[VERIFY_USER_KEY] == "decoy"

    page = client.get("/auth/verify-email/")
    assert page.status_code == 200 and "v***@example.tz" in page.content.decode()

    for _ in range(5):
        wrong = client.post("/auth/verify-email/", {"code": "123456"})
        assert "Incorrect code" in wrong.content.decode()
    assert "expired" in client.post("/auth/verify-email/", {"code": "123456"}).content.decode()
    assert "_auth_user_id" not in client.session  # never logged in


@pytest.mark.django_db
def test_decoy_page_without_a_typed_email_goes_to_login(client):
    session = client.session
    session[VERIFY_USER_KEY] = "decoy"
    session.save()
    assert client.get("/auth/verify-email/").status_code == 302


# ------------------------------------------------------------------ #10 leaked passwords
def test_leaked_password_list_is_loaded():
    assert len(_breached()) > 40000


@pytest.mark.django_db
def test_leaked_password_is_rejected_and_a_strong_one_accepted():
    leaked = next(p for p in _breached() if len(p) >= 8 and not p.isdigit())
    with pytest.raises(ValidationError) as exc:
        validate_password(leaked)
    assert any("data leaks" in m for m in exc.value.messages)
    validate_password(STRONG)


@pytest.mark.django_db
def test_register_form_rejects_a_leaked_password():
    leaked = next(p for p in _breached() if p.isalnum() and not p.isdigit())
    form = RegisterForm(
        {"role": "STUDENT", "email": "z@example.tz", "first_name": "A", "last_name": "B", "password1": leaked, "password2": leaked}
    )
    assert "password1" in form.errors


# ------------------------------------------------------------------ #11 keyless bot check
def _stamp(age_seconds):
    return TimestampSigner(salt=antibot.SALT).sign(str(int(time.time()) - age_seconds))


def _login_data(**over):
    data = {"email": "a@example.tz", "password": "whatever", "website": "", "form_ts": _stamp(30)}
    data.update(over)
    return data


@pytest.fixture
def antibot_on(settings):
    settings.ANTIBOT_ENABLED = True


def test_antibot_accepts_a_human_paced_submission(antibot_on):
    assert LoginForm(_login_data()).is_valid()


def test_antibot_rejects_a_filled_honeypot(antibot_on):
    form = LoginForm(_login_data(website="http://spam.example"))
    assert not form.is_valid()
    assert "reload the page" in str(form.non_field_errors()[0])


@pytest.mark.parametrize("stamp", ["", "garbage", "123:abc:def"])
def test_antibot_rejects_a_missing_or_forged_stamp(antibot_on, stamp):
    assert not LoginForm(_login_data(form_ts=stamp)).is_valid()


def test_antibot_rejects_a_stamp_that_is_too_fresh(antibot_on):
    assert not LoginForm(_login_data(form_ts=_stamp(0))).is_valid()


@pytest.mark.django_db
def test_register_needs_more_time_than_login(antibot_on):
    data = {
        "role": "STUDENT", "email": "z@example.tz", "first_name": "A", "last_name": "B",
        "password1": STRONG, "password2": STRONG, "website": "",
    }
    assert not RegisterForm({**data, "form_ts": _stamp(3)}).is_valid()  # under the 5 s minimum
    assert RegisterForm({**data, "form_ts": _stamp(30)}).is_valid()


def test_antibot_can_be_switched_off(settings):
    settings.ANTIBOT_ENABLED = False
    assert LoginForm({"email": "a@example.tz", "password": "x"}).is_valid()


@pytest.mark.django_db
def test_login_and_register_pages_render_the_hidden_fields(client):
    for url in ("/auth/login/", "/auth/register/"):
        html = client.get(url).content.decode()
        assert 'name="website"' in html and 'name="form_ts"' in html


@pytest.mark.django_db
def test_bot_posting_the_login_form_never_reaches_authentication(client, antibot_on):
    User.objects.create_user(email="real@example.tz", password=STRONG, role=UserRole.STUDENT)
    response = client.post("/auth/login/", {"email": "real@example.tz", "password": STRONG, "website": "x"})
    assert response.status_code == 200 and "_auth_user_id" not in client.session


# ------------------------------------------------------------------ #14 BRELA / TIN / free email
@pytest.mark.parametrize(
    "raw,expected",
    [("123456789", "123-456-789"), ("123-456-789", "123-456-789"), (" 123 456 789 ", "123-456-789"), ("", "")],
)
def test_tin_is_normalised(raw, expected):
    assert normalize_tin(raw) == expected


@pytest.mark.parametrize("raw", ["12345678", "1234567890", "abc-def-ghi"])
def test_bad_tin_is_rejected(raw):
    with pytest.raises(ValidationError):
        normalize_tin(raw)


def test_brela_number_checks():
    assert normalize_brela(" 12345/ab-1 ") == "12345/AB-1"
    assert normalize_brela("") == ""
    with pytest.raises(ValidationError):
        normalize_brela("@@")


def test_free_email_detection():
    assert is_free_email("Boss@Gmail.com") and is_free_email("x@yahoo.com")
    assert not is_free_email("info@acme.co.tz") and not is_free_email("")


def _org_form(**over):
    data = {
        "role": "ORGANIZATION", "email": "me@acme.co.tz", "first_name": "A", "last_name": "B",
        "password1": STRONG, "password2": STRONG, "organization_name": "Acme Ltd",
        "organization_email": "info@acme.co.tz", "organization_phone": "255222123456",
        "position": "HR", "phone": "255712345678",
    }
    data.update(over)
    return data


@pytest.mark.django_db
def test_organization_identifiers_are_optional():
    assert RegisterForm(_org_form()).is_valid()


@pytest.mark.django_db
def test_organization_form_normalises_and_validates_identifiers():
    ok = RegisterForm(_org_form(tin="123456789", brela_number="abc 123"))
    assert ok.is_valid() and ok.cleaned_data["tin"] == "123-456-789" and ok.cleaned_data["brela_number"] == "ABC 123"
    assert "tin" in RegisterForm(_org_form(tin="12")).errors


@pytest.mark.django_db
def test_registration_stores_the_identifiers_and_flags_free_email():
    result = process_registration_task(
        _form_data(
            email="boss@acme.co.tz", role="ORGANIZATION", organization_name="Acme Ltd",
            organization_email="acme.owner@gmail.com", organization_phone="255222123456",
            position="MD", brela_number="ABC 123", tin="123-456-789",
        )
    )
    assert result["status"] == "success"
    org = Organization.objects.get(name="Acme Ltd")
    assert (org.brela_number, org.tin) == ("ABC 123", "123-456-789")
    assert org.official_email_is_free is True
