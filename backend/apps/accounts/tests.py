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
        "email": "hr@personal.tz",
        "first_name": "Neema",
        "last_name": "Mushi",
        "phone": "255712000111",
        "role": UserRole.ORGANIZATION,
        "organization_name": "Acme Industrial Ltd",
        "organization_email": "info@acme.tz",
        "organization_phone": "255222000111",
        "position": "Human Resources Manager",
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
    return {**data, "role": "ORGANIZATION", "password_hash": make_password("Secret123!x")}


@pytest.mark.django_db
def test_organization_registration_creates_org_and_first_member_as_manager():
    from apps.accounts.tasks import process_registration_task
    from apps.companies.models import Organization, OrganizationMember, OrganizationMemberRole

    result = process_registration_task(_task_data())
    assert result["status"] == "success"
    user = User.objects.get(email="hr@personal.tz")  # the member's own login email
    assert user.full_name == "Neema Mushi" and user.phone == "255712000111"
    org = Organization.objects.get(user=user)
    assert org.name == "Acme Industrial Ltd"
    assert org.email == "info@acme.tz" and org.phone == "255222000111"  # official contact identity
    member = OrganizationMember.objects.get(organization=org, user=user)
    assert member.role == OrganizationMemberRole.OWNER  # shown as "Manager (Owner)"
    assert member.position == "Human Resources Manager"  # real-world position, separate from the role
    assert member.get_role_display().startswith("Manager")


@pytest.mark.django_db
def test_organization_registration_form_requires_organization_and_first_member_details():
    from apps.accounts.forms import RegisterForm
    from apps.accounts.tasks import process_registration_task

    assert RegisterForm(_organization_form_data()).is_valid()
    for missing in ("organization_name", "organization_email", "organization_phone", "position", "first_name", "last_name", "phone"):
        form = RegisterForm(_organization_form_data(**{missing: ""}))
        assert not form.is_valid() and missing in form.errors, missing

    student_form = RegisterForm(
        {
            "email": "student@acme.tz", "first_name": "", "last_name": "", "role": UserRole.STUDENT,
            "password1": "Secret123!x", "password2": "Secret123!x",
        }
    )
    assert not student_form.is_valid()
    assert "first_name" in student_form.errors and "last_name" in student_form.errors

    process_registration_task(_task_data())
    dup = RegisterForm(
        _organization_form_data(
            email="other@personal.tz", phone="255712000222",
            organization_name="acme industrial ltd", organization_email="INFO@acme.tz",
        )
    )
    assert not dup.is_valid()
    assert "organization_name" in dup.errors and "organization_email" in dup.errors
    # The member's own email and phone stay unique per account.
    same = RegisterForm(_organization_form_data(organization_name="Other Co", organization_email="x@other.tz"))
    # The phone is rejected generically; an existing email is deliberately NOT reported (no enumeration).
    assert not same.is_valid() and "phone" in same.errors and "email" not in same.errors


# ── IAM step 1: closed authentication holes ─────────────────────────────────

@pytest.mark.django_db
def test_registration_task_stores_hash_and_rejects_missing_hash():
    from apps.accounts.tasks import process_registration_task

    bad = _task_data()
    bad.pop("password_hash")
    bad["password1"] = "Secret123!x"
    assert process_registration_task(bad)["status"] == "error"
    assert not User.objects.filter(email="hr@personal.tz").exists()

    assert process_registration_task(_task_data())["status"] == "success"
    assert User.objects.get(email="hr@personal.tz").check_password("Secret123!x")


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
    for _ in range(4):
        assert client.post("/auth/login/", {"email": "l@x.tz", "password": "bad"}).status_code == 200
    # The fifth failure trips the lock and immediately shows a live countdown.
    tripped = client.post("/auth/login/", {"email": "l@x.tz", "password": "bad"})
    body = tripped.content.decode()
    assert tripped.status_code == 429 and 'data-countdown="' in body and "Too many failed attempts" in body
    assert 0 < int(tripped["Retry-After"]) <= 300
    # Locked: even the correct password is refused, and the message is generic.
    locked = client.post("/auth/login/", {"email": "l@x.tz", "password": "Secret123!x"})
    assert locked.status_code == 429 and "_auth_user_id" not in client.session
    # The rate limiter answers here, but the timer still reflects the (longer) lock.
    assert int(locked["Retry-After"]) > 30
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
    assert "can't be used" in form.errors["phone"][0]  # generic: never says the number is registered
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


# ── IAM step 3: sessions ────────────────────────────────────────────────────
import time as _time

from django.conf import settings as dj_settings


def _login(client, email="s@x.tz", password="Secret123!x"):
    return client.post("/auth/login/", {"email": email, "password": password})


def _student(email="s@x.tz"):
    return User.objects.create_user(
        email=email, password="Secret123!x", role=UserRole.STUDENT, email_verified=True
    )


@pytest.mark.django_db
def test_session_cookie_flags_and_lifetime():
    assert dj_settings.SESSION_COOKIE_AGE == 600
    assert dj_settings.SESSION_COOKIE_HTTPONLY is True
    assert dj_settings.SESSION_COOKIE_SAMESITE in ("Lax", "Strict")
    _student()
    resp = _login(Client())
    morsel = resp.cookies[dj_settings.SESSION_COOKIE_NAME]
    assert morsel["httponly"] and morsel["samesite"] == "Lax" and int(morsel["max-age"]) <= 600


@pytest.mark.django_db
def test_session_key_is_long_random_and_rotates_on_login():
    c = Client()
    c.get("/auth/login/")
    _student()
    c.post("/auth/login/", {"email": "nobody@x.tz", "password": "x"})  # creates an anonymous session
    before = c.cookies.get(dj_settings.SESSION_COOKIE_NAME)
    before = before.value if before else ""
    _login(c)
    after = c.cookies[dj_settings.SESSION_COOKIE_NAME].value
    assert before != after and len(after) >= 32  # 32 chars of a 36-symbol alphabet >= 165 bits


@pytest.mark.django_db
def test_second_device_logs_the_first_out_in_both_directions():
    user = _student()
    a, b = Client(), Client()
    assert _login(a).status_code == 302
    assert a.get("/auth/session-status/").json()["valid"] is True
    assert _login(b).status_code == 302
    assert a.get("/auth/session-status/").status_code == 401  # device A is out
    assert b.get("/auth/session-status/").json()["valid"] is True
    assert _login(a).status_code == 302  # and back again
    assert b.get("/auth/session-status/").status_code == 401
    user.refresh_from_db()
    from django.contrib.sessions.models import Session

    assert Session.objects.filter(session_key=user.current_session_key).exists()
    # Old rows are destroyed on the server, not merely forgotten by the browser.
    assert Session.objects.filter(session_key__in=[
        a.cookies[dj_settings.SESSION_COOKIE_NAME].value,
        b.cookies[dj_settings.SESSION_COOKIE_NAME].value,
    ]).count() == 1


@pytest.mark.django_db
def test_session_dies_after_ten_minutes_even_if_never_used_again(monkeypatch):
    _student()
    c = Client()
    _login(c)
    assert c.get("/auth/session-status/").json()["valid"] is True
    key = c.cookies[dj_settings.SESSION_COOKIE_NAME].value
    real = _time.time()
    monkeypatch.setattr("apps.accounts.sessions.time.time", lambda: real + 601)
    assert c.get("/auth/session-status/").status_code == 401
    page = c.get("/auth/notification-settings/")
    assert page.status_code == 302 and "/auth/login/" in page.url
    from django.contrib.sessions.models import Session

    # The server destroyed it; the cookie value alone is worthless.
    assert not Session.objects.filter(session_key=key).exists()


@pytest.mark.django_db
def test_logout_destroys_server_session_and_stale_cookie_is_rejected():
    _student()
    c = Client()
    _login(c)
    stolen = c.cookies[dj_settings.SESSION_COOKIE_NAME].value
    c.get("/auth/logout/")
    from django.contrib.sessions.models import Session

    assert not Session.objects.filter(session_key=stolen).exists()
    thief = Client()
    thief.cookies[dj_settings.SESSION_COOKIE_NAME] = stolen
    assert thief.get("/auth/session-status/").status_code == 401


@pytest.mark.django_db
def test_authenticated_pages_are_not_cached():
    _student()
    c = Client()
    _login(c)
    cc = c.get("/auth/notification-settings/")["Cache-Control"]
    assert "no-store" in cc and "private" in cc


@pytest.mark.django_db
def test_organization_cap_counts_people_and_a_members_second_device_replaces_the_first():
    from apps.companies.models import MembershipStatus, OrganizationMember
    from apps.companies.services import create_organization

    owner = User.objects.create_user(
        email="o@x.tz", password="Secret123!x", role=UserRole.ORGANIZATION, email_verified=True
    )
    org = create_organization(owner, "Acme Co")
    emails = ["o@x.tz"]
    for i in range(4):
        u = User.objects.create_user(
            email=f"m{i}@x.tz", password="Secret123!x", role=UserRole.ORGANIZATION, email_verified=True
        )
        OrganizationMember.objects.create(
            organization=org, user=u, role="RECRUITER", membership_status=MembershipStatus.ACTIVE
        )
        emails.append(f"m{i}@x.tz")
    clients = []
    for email in emails:
        c = Client()
        assert _login(c, email).status_code == 302
        clients.append(c)
    # Five people are logged in at once; each has exactly one session.
    from apps.companies.models import OrganizationSession

    assert OrganizationSession.objects.filter(organization=org).count() == 5
    again = Client()
    assert _login(again, "m3@x.tz").status_code == 302  # not blocked by the cap
    assert clients[4].get("/auth/session-status/").status_code == 401
    assert OrganizationSession.objects.filter(organization=org).count() == 5


@pytest.mark.django_db
def test_login_and_logout_are_audited():
    from apps.core.models import AuditLog

    _student()
    c = Client()
    _login(c)
    c.get("/auth/logout/")
    actions = list(AuditLog.objects.filter(module="Security").values_list("action", flat=True))
    assert "Login" in actions and "Logout" in actions


def _at(monkeypatch, real, seconds):
    monkeypatch.setattr("apps.accounts.sessions.time.time", lambda: real + seconds)


@pytest.mark.django_db
def test_activity_restarts_the_ten_minutes(monkeypatch):
    _student()
    c = Client()
    _login(c)
    real = _time.time()
    _at(monkeypatch, real, 400)  # 6m40s in: the person moves the mouse
    assert c.post("/auth/session-extend/").json()["expires_in"] >= 595
    _at(monkeypatch, real, 800)  # 13m20s after login, but only 6m40s since the last movement
    assert c.get("/auth/session-status/").status_code == 200
    _at(monkeypatch, real, 400 + 601)  # ten idle minutes after the last movement
    assert c.get("/auth/session-status/").status_code == 401


@pytest.mark.django_db
def test_polling_status_never_extends_the_session(monkeypatch):
    _student()
    c = Client()
    _login(c)
    real = _time.time()
    for t in (200, 400, 590):  # an untouched tab keeps polling
        _at(monkeypatch, real, t)
        assert c.get("/auth/session-status/").status_code == 200
    _at(monkeypatch, real, 601)
    assert c.get("/auth/session-status/").status_code == 401


@pytest.mark.django_db
def test_activity_cannot_extend_past_the_absolute_ceiling(monkeypatch, settings):
    settings.SESSION_ABSOLUTE_MAX_SECONDS = 1000
    _student()
    c = Client()
    _login(c)
    real = _time.time()
    for t in (500, 900):
        _at(monkeypatch, real, t)
        assert c.post("/auth/session-extend/").status_code == 200
    _at(monkeypatch, real, 1001)  # idle for only 101 s, but past the ceiling
    assert c.get("/auth/session-status/").status_code == 401


@pytest.mark.django_db
def test_extend_needs_a_signed_in_session_and_post():
    assert Client().post("/auth/session-extend/").status_code == 401
    _student()
    c = Client()
    _login(c)
    assert c.get("/auth/session-extend/").status_code == 405
