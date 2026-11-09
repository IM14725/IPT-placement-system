import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core import signing
from rest_framework.authtoken.models import Token

from apps.accounts.models import UserRole

User = get_user_model()


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """The fake Redis is shared across tests; start each one with full rate-limit buckets."""
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield



@pytest.mark.django_db
def test_superuser_role_is_super_admin():
    user = User.objects.create_superuser(email="admin@x.tz", password="Secret123!")
    assert user.is_superuser
    assert user.role == UserRole.SUPER_ADMIN
    assert user.is_platform_admin


@pytest.mark.django_db
def test_role_helpers():
    student = User.objects.create_user(email="s@x.tz", password="Secret123!", role=UserRole.STUDENT)
    organization = User.objects.create_user(email="c@x.tz", password="Secret123!", role=UserRole.ORGANIZATION)
    assert student.is_student and not student.is_organization
    assert organization.is_organization and not organization.is_student


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

def _organization_form_data(**overrides):
    data = {
        "email": "owner@acme.tz",
        "first_name": "",
        "last_name": "",
        "role": UserRole.ORGANIZATION,
        "organization_name": "Acme Industrial Ltd",
        "password1": "Secret123!x",
        "password2": "Secret123!x",
    }
    data.update(overrides)
    return data


def _task_data(**overrides):
    """What the register view hands to the task: a password hash, never the password."""
    data = _organization_form_data(**overrides)
    data.pop("password1")
    data.pop("password2")
    return {**data, "role": "ORGANIZATION", "phone": "", "password_hash": make_password("Secret123!x")}


@pytest.mark.django_db
def test_organization_registration_uses_only_organization_name_and_creates_member():
    from apps.accounts.tasks import process_registration_task
    from apps.companies.models import Organization, OrganizationMember, OrganizationMemberRole

    result = process_registration_task(
        _task_data(first_name="Ignored", last_name="Ignored")
    )
    assert result["status"] == "success"
    user = User.objects.get(email="owner@acme.tz")
    assert (user.first_name, user.last_name) == ("", "")
    assert user.full_name == "Acme Industrial Ltd"
    org = Organization.objects.get(user=user)
    assert org.name == "Acme Industrial Ltd"
    assert OrganizationMember.objects.filter(
        organization=org, user=user, role=OrganizationMemberRole.OWNER
    ).exists()


@pytest.mark.django_db
def test_organization_registration_form_does_not_require_person_name_and_enforces_unique_org():
    from apps.accounts.forms import RegisterForm
    from apps.accounts.tasks import process_registration_task

    form = RegisterForm(_organization_form_data(first_name="", last_name=""))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["first_name"] == ""
    assert form.cleaned_data["last_name"] == ""

    student_form = RegisterForm(
        {
            "email": "student@acme.tz",
            "first_name": "",
            "last_name": "",
            "role": UserRole.STUDENT,
            "password1": "Secret123!x",
            "password2": "Secret123!x",
        }
    )
    assert not student_form.is_valid()
    assert "first_name" in student_form.errors and "last_name" in student_form.errors

    process_registration_task(_task_data())
    dup = RegisterForm(_organization_form_data(email="other@acme.tz", organization_name="acme industrial ltd"))
    assert not dup.is_valid()
    assert "organization_name" in dup.errors


# ── IAM step 1: closed authentication holes ─────────────────────────────────

@pytest.mark.django_db
def test_registration_task_stores_hash_and_rejects_missing_hash():
    from apps.accounts.tasks import process_registration_task

    bad = _task_data()
    bad.pop("password_hash")
    bad["password1"] = "Secret123!x"
    assert process_registration_task(bad)["status"] == "error"
    assert not User.objects.filter(email="owner@acme.tz").exists()

    assert process_registration_task(_task_data())["status"] == "success"
    assert User.objects.get(email="owner@acme.tz").check_password("Secret123!x")


@pytest.mark.django_db
def test_login_is_inline_and_never_queues_the_password(client, monkeypatch):
    User.objects.create_user(email="in@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True)
    called = []
    monkeypatch.setattr(
        "celery.app.task.Task.delay", lambda *a, **k: called.append((a, k))
    )
    bad = client.post("/auth/login/", {"email": "in@x.tz", "password": "wrong"})
    assert bad.status_code == 200 and "Invalid email or password." in bad.content.decode()
    ok = client.post("/auth/login/", {"email": "in@x.tz", "password": "Secret123!x"})
    assert ok.status_code == 302
    assert "_auth_user_id" in client.session
    assert called == []


@pytest.mark.django_db
def test_task_status_is_bound_to_the_submitting_session(client):
    resp = client.get("/auth/task-status/not-mine/")
    assert resp.json()["status"] == "ERROR"


@pytest.mark.django_db
def test_finalize_token_must_be_issued_to_session_fresh_and_single_use(client):
    user = User.objects.create_user(email="f@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True)
    signer = signing.TimestampSigner(salt="accounts.finalize-auth")
    token = signer.sign(str(user.id))

    # A valid signature that was never issued to this session is rejected.
    client.post("/auth/finalize/", {"token": token})
    assert "_auth_user_id" not in client.session

    # Issued to this session: works once.
    session = client.session
    session["auth_finalize_token"] = token
    session.save()
    resp = client.post("/auth/finalize/", {"token": token})
    assert resp.status_code == 302 and client.session["_auth_user_id"] == str(user.id)

    client.logout()
    client.post("/auth/finalize/", {"token": token})
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_finalize_token_expires(client, monkeypatch):
    user = User.objects.create_user(email="e@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True)
    token = signing.TimestampSigner(salt="accounts.finalize-auth").sign(str(user.id))
    session = client.session
    session["auth_finalize_token"] = token
    session.save()
    import time

    real = time.time()
    monkeypatch.setattr("django.core.signing.time.time", lambda: real + 120)
    client.post("/auth/finalize/", {"token": token})
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_api_token_endpoints_and_token_auth_are_gone(client):
    from django.conf import settings

    assert client.post("/api/auth/token/", {"username": "a", "password": "b"}).status_code == 404
    assert client.get("/api/auth/my-token/").status_code == 404
    classes = settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]
    assert "rest_framework.authentication.TokenAuthentication" not in classes


@pytest.mark.django_db
def test_ws_ticket_is_per_user_short_lived_in_redis(client):
    from apps.core.redis_client import get_redis

    assert client.get("/api/auth/ws-ticket/").status_code == 302  # login required
    user = User.objects.create_user(email="w@x.tz", password="Secret123!x", role=UserRole.STUDENT)
    client.force_login(user)
    data = client.get("/api/auth/ws-ticket/").json()
    assert data["user_id"] == str(user.id)
    key = f"ipt:wsticket:{data['ticket']}"
    assert get_redis().get(key) in (str(user.id), str(user.id).encode())
    assert 0 < get_redis().ttl(key) <= 60


# ── IAM step 2: OTP, lockout, unique phone ──────────────────────────────────
import re
from datetime import timedelta

from django.core import mail
from django.test import Client, override_settings
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import OneTimeCode, OtpChannel, OtpPurpose


def _code_from_outbox():
    return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)


def _wrong_code(code):
    return "000000" if code != "000000" else "111111"



def _start_recovery(client, email, channel):
    """Step 1 (email) then step 2 (method). Returns the step-2 response."""
    first = client.post("/auth/forgot-password/", {"email": email})
    assert first.status_code == 302 and first.url.endswith("/auth/reset-password/method/")
    return client.post("/auth/reset-password/method/", {"channel": channel})


def _verified_user(email="v@x.tz", phone="255712345678"):
    return User.objects.create_user(
        email=email, password="Secret123!x", phone=phone, role=UserRole.STUDENT,
        email_verified=True, phone_verified=True,
    )


@pytest.mark.django_db
def test_otp_is_hashed_single_use_and_expires():
    user = _verified_user()
    assert otp.send_otp(user, OtpPurpose.RECOVERY, OtpChannel.EMAIL).sent
    code = _code_from_outbox()
    row = OneTimeCode.objects.get()
    assert code not in row.code_hash and len(row.code_hash) == 64
    assert otp.verify_otp(user, OtpPurpose.RECOVERY, code) == otp.OK
    assert otp.verify_otp(user, OtpPurpose.RECOVERY, code) == otp.EXPIRED  # single use

    otp.send_otp(user, OtpPurpose.RECOVERY, OtpChannel.EMAIL)
    code = _code_from_outbox()
    OneTimeCode.objects.filter(consumed_at__isnull=True).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert otp.verify_otp(user, OtpPurpose.RECOVERY, code) == otp.EXPIRED


@pytest.mark.django_db
def test_otp_lifetime_is_five_minutes():
    user = _verified_user()
    otp.send_otp(user, OtpPurpose.RECOVERY, OtpChannel.EMAIL)
    row = OneTimeCode.objects.get()
    assert timedelta(minutes=4, seconds=50) < row.expires_at - row.created_at <= timedelta(minutes=5, seconds=1)


@pytest.mark.django_db
def test_resending_invalidates_previous_code_and_guesses_are_limited():
    user = _verified_user()
    otp.send_otp(user, OtpPurpose.RECOVERY, OtpChannel.EMAIL)
    old = _code_from_outbox()
    otp.send_otp(user, OtpPurpose.RECOVERY, OtpChannel.EMAIL)
    new = _code_from_outbox()
    assert OneTimeCode.objects.filter(consumed_at__isnull=True).count() == 1
    if old != new:
        assert otp.verify_otp(user, OtpPurpose.RECOVERY, old) == otp.INVALID
    wrong = _wrong_code(new)
    # The attempt above (if any) counts toward the limit; burn the rest.
    while OneTimeCode.objects.filter(consumed_at__isnull=True).exists():
        assert otp.verify_otp(user, OtpPurpose.RECOVERY, wrong) in (otp.INVALID, otp.EXPIRED)
    # Out of guesses: even the right code no longer works.
    assert otp.verify_otp(user, OtpPurpose.RECOVERY, new) == otp.EXPIRED


@pytest.mark.django_db
@override_settings(RATE_LIMIT_ENABLED=True)
def test_otp_resend_is_rate_limited():
    user = _verified_user()
    results = [otp.send_otp(user, OtpPurpose.RECOVERY, OtpChannel.EMAIL) for _ in range(5)]
    assert [r.sent for r in results[:3]] == [True, True, True]
    assert not results[3].sent and results[3].retry_after > 0


def _register_and_finalize(client, email="new@x.tz"):
    from apps.accounts.tasks import process_registration_task

    result = process_registration_task(
        {
            "email": email, "role": "STUDENT", "first_name": "A", "last_name": "B",
            "phone": "", "password_hash": make_password("Secret123!x"),
        }
    )
    user = User.objects.get(id=result["user_id"])
    token = signing.TimestampSigner(salt="accounts.finalize-auth").sign(str(user.id))
    session = client.session
    session["auth_finalize_token"] = token
    session.save()
    return user, client.post("/auth/finalize/", {"token": token})


@pytest.mark.django_db
def test_registration_requires_email_otp_before_login(client):
    user, resp = _register_and_finalize(client)
    assert not user.email_verified
    assert resp.status_code == 302 and resp.url.endswith("/auth/verify-email/")
    assert "_auth_user_id" not in client.session  # created, but not logged in yet
    assert mail.outbox[-1].to == ["new@x.tz"]  # email only, no channel choice

    bad = client.post("/auth/verify-email/", {"code": _wrong_code(_code_from_outbox())})
    assert bad.status_code == 200 and "_auth_user_id" not in client.session
    ok = client.post("/auth/verify-email/", {"code": _code_from_outbox()})
    assert ok.status_code == 302 and client.session["_auth_user_id"] == str(user.id)
    user.refresh_from_db()
    assert user.email_verified


@pytest.mark.django_db
def test_login_with_unverified_email_is_routed_to_verification(client):
    User.objects.create_user(email="u@x.tz", password="Secret123!x", role=UserRole.STUDENT)
    resp = client.post("/auth/login/", {"email": "u@x.tz", "password": "Secret123!x"})
    assert resp.status_code == 302 and resp.url.endswith("/auth/verify-email/")
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_recovery_by_email_resets_password_and_signs_out_other_sessions(client):
    user = _verified_user()
    other = Client()
    other.force_login(user)
    assert other.get("/auth/notification-settings/").status_code == 200

    resp = _start_recovery(client, user.email, "EMAIL")
    assert resp.status_code == 302 and resp.url.endswith("/auth/reset-password/verify/")
    resp = client.post("/auth/reset-password/verify/", {"code": _code_from_outbox()})
    assert resp.url.endswith("/auth/reset-password/")
    resp = client.post(
        "/auth/reset-password/", {"password1": "Brand-new-Pass-77", "password2": "Brand-new-Pass-77"}
    )
    assert resp.status_code == 302
    user.refresh_from_db()
    assert user.check_password("Brand-new-Pass-77")
    # Django ties sessions to the password hash: the old session is now dead.
    after = other.get("/auth/notification-settings/")
    assert after.status_code == 302 and "/auth/login/" in after.url


@pytest.mark.django_db
def test_recovery_by_sms_goes_to_phone_not_mailbox(client, capsys):
    user = _verified_user()
    mail.outbox.clear()
    resp = _start_recovery(client, user.email, "SMS")
    assert resp.status_code == 302
    assert mail.outbox == []
    assert "[SMS:MOCK] -> 255712345678" in capsys.readouterr().out


@pytest.mark.django_db
def test_recovery_is_generic_and_only_uses_verified_channels(client, capsys):
    unverified = User.objects.create_user(
        email="p@x.tz", password="Secret123!x", phone="255755555555",
        role=UserRole.STUDENT, email_verified=True, phone_verified=False,
    )
    sms = _start_recovery(client, unverified.email, "SMS")
    ghost = _start_recovery(client, "ghost@x.tz", "EMAIL")
    assert sms.status_code == ghost.status_code == 302 and sms.url == ghost.url
    assert "[SMS:MOCK]" not in capsys.readouterr().out and mail.outbox == []
    bad = client.post("/auth/reset-password/verify/", {"code": "123456"})
    assert bad.status_code == 200 and "pwd_reset_verified_user_id" not in client.session


@pytest.mark.django_db
def test_login_lockout_after_five_failures_then_lifts():
    from apps.core.redis_client import get_redis

    client = Client()
    User.objects.create_user(
        email="l@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True
    )
    for _ in range(5):
        assert client.post("/auth/login/", {"email": "l@x.tz", "password": "bad"}).status_code == 200
    # Locked: even the correct password is refused, and the message is generic.
    locked = client.post("/auth/login/", {"email": "l@x.tz", "password": "Secret123!x"})
    assert locked.status_code == 429 and "_auth_user_id" not in client.session
    keys = list(get_redis().scan_iter("*lockout:login:l@x.tz:lock"))
    assert len(keys) == 1 and 0 < get_redis().ttl(keys[0]) <= 300
    # After the 5 minutes pass (key expires) the same account logs in: it was never disabled.
    get_redis().delete(keys[0])
    # The bucket rate limiter (5 tokens, 1 per 30s) has long refilled by then too.
    for key in get_redis().scan_iter("*:rl:login-*"):
        get_redis().delete(key)
    ok = client.post("/auth/login/", {"email": "l@x.tz", "password": "Secret123!x"})
    assert ok.status_code == 302 and "_auth_user_id" in client.session


@pytest.mark.django_db
def test_lockout_does_not_reveal_whether_account_exists(client):
    for _ in range(5):
        client.post("/auth/login/", {"email": "ghost@x.tz", "password": "bad"})
    assert client.post("/auth/login/", {"email": "ghost@x.tz", "password": "bad"}).status_code == 429


@pytest.mark.django_db
def test_phone_numbers_are_unique_across_formats():
    from django.db import IntegrityError, transaction

    from apps.accounts.forms import RegisterForm

    _verified_user(phone="+255712345678")
    form = RegisterForm(
        {
            "email": "z@x.tz", "first_name": "A", "last_name": "B", "role": UserRole.STUDENT,
            "phone": "255712345678", "password1": "Secret123!x", "password2": "Secret123!x",
        }
    )
    assert not form.is_valid()
    assert "already associated with an account" in form.errors["phone"][0]
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(email="y@x.tz", password="Secret123!x", phone="255712345678")
    # Blank phones may repeat.
    User.objects.create_user(email="b1@x.tz", password="Secret123!x")
    User.objects.create_user(email="b2@x.tz", password="Secret123!x")


@pytest.mark.django_db
def test_email_step_goes_to_method_choice_without_sending_or_revealing(client, capsys):
    user = _verified_user()
    mail.outbox.clear()
    known = client.post("/auth/forgot-password/", {"email": user.email})
    ghost = Client().post("/auth/forgot-password/", {"email": "ghost@x.tz"})
    assert known.status_code == ghost.status_code == 302
    assert known.url == ghost.url == "/auth/reset-password/method/"
    assert mail.outbox == [] and "[SMS:MOCK]" not in capsys.readouterr().out  # nothing sent yet

    page = client.get("/auth/reset-password/method/")
    body = page.content.decode()
    assert page.status_code == 200 and "EMAIL" in body and "SMS" in body

    # The method screen and code screen cannot be reached without the earlier steps.
    fresh = Client()
    assert fresh.get("/auth/reset-password/method/").url.endswith("/auth/forgot-password/")
    assert fresh.get("/auth/reset-password/verify/").url.endswith("/auth/forgot-password/")
    assert client.get("/auth/reset-password/verify/").url.endswith("/auth/reset-password/method/")
