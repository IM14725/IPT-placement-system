"""IAM step 7: verified email/phone changes, unique contact info, forced password change."""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import OneTimeCode, OtpPurpose, UserRole

User = get_user_model()
PW = "Secret123!x"


@pytest.fixture(autouse=True)
def _reset_redis_state():
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield


def _user(email="u@x.tz", phone="255712345678", **kw):
    return User.objects.create_user(
        email=email, password=PW, phone=phone, role=kw.pop("role", UserRole.STUDENT),
        email_verified=True, phone_verified=kw.pop("phone_verified", True), **kw,
    )


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _mail_code():
    return re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)


def _wrong(code):
    return "000000" if code != "000000" else "111111"


@pytest.mark.django_db
def test_account_page_requires_login_and_lists_status(client):
    assert client.get("/auth/account/").status_code == 302
    user = _user()
    page = _client(user).get("/auth/account/")
    assert page.status_code == 200 and b"Verified" in page.content and user.email.encode() in page.content


@pytest.mark.django_db
def test_email_change_needs_otp_on_the_new_address_before_anything_is_saved():
    user = _user()
    c = _client(user)
    mail.outbox.clear()
    resp = c.post("/auth/account/email/", {"email": "New@X.tz", "current_password": PW})
    assert resp.status_code == 302 and resp.url == "/auth/account/verify/email/"
    assert mail.outbox[-1].to == ["new@x.tz"]  # the NEW address gets the code, not the old one
    user.refresh_from_db()
    assert user.email == "u@x.tz"  # nothing saved yet

    # Incorrect OTP rejects the change.
    bad = c.post("/auth/account/verify/email/", {"code": _wrong(_mail_code())})
    assert bad.status_code == 200 and "not saved" in bad.content.decode()
    user.refresh_from_db()
    assert user.email == "u@x.tz"

    ok = c.post("/auth/account/verify/email/", {"code": _mail_code()})
    assert ok.status_code == 302
    user.refresh_from_db()
    assert user.email == "new@x.tz" and user.email_verified
    # Still signed in after the change, and the code cannot be replayed.
    assert c.get("/auth/session-status/").json()["valid"] is True
    assert otp.verify_otp(user, OtpPurpose.CHANGE_EMAIL, _mail_code()) == otp.EXPIRED


@pytest.mark.django_db
def test_expired_email_otp_requires_a_new_code():
    user = _user()
    c = _client(user)
    c.post("/auth/account/email/", {"email": "new@x.tz", "current_password": PW})
    code = _mail_code()
    OneTimeCode.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(seconds=1))
    resp = c.post("/auth/account/verify/email/", {"code": code})
    # No pending change any more: the user is sent back to start again / request another code.
    assert resp.status_code == 302 and resp.url == "/auth/account/"
    user.refresh_from_db()
    assert user.email == "u@x.tz"
    c.post("/auth/account/email/", {"email": "new@x.tz", "current_password": PW})
    assert c.post("/auth/account/verify/email/", {"code": _mail_code()}).status_code == 302
    user.refresh_from_db()
    assert user.email == "new@x.tz"


@pytest.mark.django_db
def test_resend_invalidates_the_previous_code():
    user = _user()
    c = _client(user)
    c.post("/auth/account/email/", {"email": "new@x.tz", "current_password": PW})
    first = _mail_code()
    c.post("/auth/account/verify/email/", {"action": "resend"})
    second = _mail_code()
    if first != second:
        c.post("/auth/account/verify/email/", {"code": first})
        user.refresh_from_db()
        assert user.email == "u@x.tz"
    c.post("/auth/account/verify/email/", {"code": second})
    user.refresh_from_db()
    assert user.email == "new@x.tz"


@pytest.mark.django_db
def test_duplicate_email_is_rejected_with_the_agreed_message_and_no_leak():
    _user("taken@x.tz", phone="255700000001")
    user = _user()
    c = _client(user)
    mail.outbox.clear()
    resp = c.post("/auth/account/email/", {"email": "TAKEN@x.tz", "current_password": PW}, follow=True)
    text = resp.content.decode()
    assert "This email address is already associated with an account. Please use another email address." in text
    assert mail.outbox == []  # no code is sent for an address that is taken
    assert "255700000001" not in text
    user.refresh_from_db()
    assert user.email == "u@x.tz"


@pytest.mark.django_db
def test_email_change_requires_the_current_password():
    user = _user()
    resp = _client(user).post("/auth/account/email/", {"email": "new@x.tz", "current_password": "nope"}, follow=True)
    assert "current password is incorrect" in resp.content.decode()
    assert OneTimeCode.objects.count() == 0


@pytest.mark.django_db
def test_phone_change_sms_goes_to_new_number_and_marks_it_verified(capsys):
    user = _user(phone="255712345678", phone_verified=False)
    c = _client(user)
    resp = c.post("/auth/account/phone/", {"phone": "+255787000111", "current_password": PW})
    assert resp.status_code == 302 and resp.url == "/auth/account/verify/phone/"
    out = capsys.readouterr().out
    assert "[SMS:MOCK] -> 255787000111" in out
    code = re.search(r"code is (\d{6})", out).group(1)
    user.refresh_from_db()
    assert user.phone == "255712345678"  # unchanged until verified
    bad = c.post("/auth/account/verify/phone/", {"code": _wrong(code)})
    assert bad.status_code == 200
    user.refresh_from_db()
    assert user.phone == "255712345678" and not user.phone_verified
    assert c.post("/auth/account/verify/phone/", {"code": code}).status_code == 302
    user.refresh_from_db()
    assert user.phone == "255787000111" and user.phone_verified


@pytest.mark.django_db
def test_duplicate_phone_is_rejected_in_any_format(capsys):
    _user("other@x.tz", phone="255787000111")
    user = _user()
    resp = _client(user).post("/auth/account/phone/", {"phone": "+255787000111", "current_password": PW}, follow=True)
    assert "This phone number is already associated with an account. Please use another phone number." in resp.content.decode()
    assert "[SMS:MOCK]" not in capsys.readouterr().out
    user.refresh_from_db()
    assert user.phone == "255712345678"


@pytest.mark.django_db
def test_verifying_the_current_unverified_phone_works(capsys):
    user = _user(phone="255712345678", phone_verified=False)
    c = _client(user)
    c.post("/auth/account/phone/", {"phone": "255712345678", "current_password": PW})
    code = re.search(r"code is (\d{6})", capsys.readouterr().out).group(1)
    c.post("/auth/account/verify/phone/", {"code": code})
    user.refresh_from_db()
    assert user.phone == "255712345678" and user.phone_verified


@pytest.mark.django_db
def test_student_cannot_apply_until_phone_is_verified():
    from apps.locations.models import District, Region
    from apps.slots.models import Slot
    from apps.students.models import StudentProfile, University, VerificationStatus
    from apps.companies.services import create_organization

    owner = _user("org@x.tz", phone="255700000009", role=UserRole.ORGANIZATION)
    org = create_organization(owner, "Org Ltd")
    district = District.objects.create(name="Ilala", region=Region.objects.create(name="Dar", slug="dar"))
    slot = Slot.objects.create(
        organization=org, title="S", industry="T", role_type="Internship", district=district, capacity=1
    )
    student = _user("stu@x.tz", phone_verified=False)
    StudentProfile.objects.create(
        user=student, university=University.objects.create(name="UDSM"), course="CS",
        academic_year=3, verification_status=VerificationStatus.APPROVED,
    )
    c = _client(student)
    resp = c.get(f"/student/apply/{slot.id}/")
    assert resp.status_code == 302 and resp.url == "/auth/account/"
    User.objects.filter(pk=student.pk).update(phone_verified=True)
    assert c.get(f"/student/apply/{slot.id}/").status_code == 200


# ── forced password change (temporary passwords) ────────────────────────────

@pytest.mark.django_db
def test_temporary_password_must_be_replaced_before_anything_else():
    user = _user()
    User.objects.filter(pk=user.pk).update(must_change_password=True)
    c = Client()
    resp = c.post("/auth/login/", {"email": user.email, "password": PW})
    assert resp.status_code == 302
    # Every page now leads to the change screen, except logout/status.
    for path in ("/student/dashboard/", "/auth/account/", "/"):
        r = c.get(path)
        assert r.status_code == 302 and r.url == "/auth/change-password/", path
    assert c.get("/auth/change-password/").status_code == 200
    assert c.get("/auth/session-status/").status_code == 200

    # Cannot keep the temporary password or choose a weak one.
    same = c.post("/auth/change-password/", {"current_password": PW, "password1": PW, "password2": PW})
    assert same.status_code == 200
    wrong = c.post("/auth/change-password/", {"current_password": "bad", "password1": "Brand-new-77x", "password2": "Brand-new-77x"})
    assert wrong.status_code == 200
    ok = c.post("/auth/change-password/", {"current_password": PW, "password1": "Brand-new-77x", "password2": "Brand-new-77x"})
    assert ok.status_code == 302 and ok.url != "/auth/change-password/"
    user.refresh_from_db()
    assert not user.must_change_password and user.check_password("Brand-new-77x") and not user.check_password(PW)
    # Still signed in (session id was re-synced) and free to use the site.
    assert c.get("/auth/account/").status_code == 200
    assert Client().post("/auth/login/", {"email": user.email, "password": PW}).status_code == 200  # old one is dead


@pytest.mark.django_db
def test_expired_temporary_password_cannot_sign_in():
    user = _user()
    User.objects.filter(pk=user.pk).update(
        must_change_password=True, temp_password_expires_at=timezone.now() - timedelta(minutes=1)
    )
    resp = Client().post("/auth/login/", {"email": user.email, "password": PW})
    assert resp.status_code == 200 and "temporary password has expired" in resp.content.decode()


@pytest.mark.django_db
def test_org_member_added_by_manager_gets_a_temporary_password_flow():
    from apps.companies.services import add_organization_member, create_organization

    owner = _user("o@x.tz", phone="255700000010", role=UserRole.ORGANIZATION)
    org = create_organization(owner, "Org Ltd")
    member = add_organization_member(org, "mem@x.tz", PW, first_name="A", last_name="B", added_by=owner).user
    assert member.must_change_password and not member.email_verified
    c = Client()
    resp = c.post("/auth/login/", {"email": "mem@x.tz", "password": PW})
    assert resp.url == "/auth/verify-email/"  # proves they own their own email first
    c.post("/auth/verify-email/", {"code": _mail_code()})
    assert c.get("/organization/dashboard/").url == "/auth/change-password/"


@pytest.mark.django_db
def test_password_login_voids_a_pending_recovery_code():
    """An organization manager who asks for a reset code, then remembers the password."""
    user = _user(email="mgr@x.tz", role=UserRole.ORGANIZATION)
    c = Client()
    c.post("/auth/forgot-password/", {"email": user.email})
    c.post("/auth/reset-password/method/", {"channel": "EMAIL"})
    code = _mail_code()
    assert OneTimeCode.objects.filter(user=user, purpose=OtpPurpose.RECOVERY, consumed_at__isnull=True).exists()

    resp = c.post("/auth/login/", {"email": user.email, "password": PW})
    assert resp.status_code == 302
    assert "_auth_user_id" in c.session

    assert not OneTimeCode.objects.filter(
        user=user, purpose=OtpPurpose.RECOVERY, consumed_at__isnull=True
    ).exists()
    assert otp.verify_otp(user, OtpPurpose.RECOVERY, code) == otp.EXPIRED


@pytest.mark.django_db
def test_register_errors_name_each_bad_field_and_keep_what_was_typed():
    resp = Client().post(
        "/auth/register/",
        {
            "role": UserRole.STUDENT,
            "email": "not-an-email",
            "first_name": "Amina",
            "last_name": "",
            "phone": "0712345678",
            "password1": PW,
            "password2": "different",
            "accept_terms": "on",
        },
    )
    html = resp.content.decode()
    assert resp.status_code == 200
    assert "form-error-summary" not in html  # no banner: the fields themselves carry the errors
    assert html.count("ipt-field-box is-error") == 4  # email, last name, phone, confirm password
    assert html.count('<p class="ipt-field-error">') == 4
    assert 'value="not-an-email"' in html and 'value="Amina"' in html and 'value="0712345678"' in html
    assert re.search(r'id="id_accept_terms"[^>]*\schecked', html)  # the ticked box survives
    assert PW not in html  # passwords are never echoed back
