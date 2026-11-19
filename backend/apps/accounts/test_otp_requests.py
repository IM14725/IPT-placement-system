"""SMS delivery failures, verifying the saved phone, and the admin "OTP requests" page."""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts import otp
from apps.accounts.models import (
    AdminRole,
    OneTimeCode,
    OtpChannel,
    OtpDelivery,
    OtpOutcome,
    OtpPurpose,
    UserRole,
)
from apps.core import providers
from apps.core.models import AuditLog

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


def _login(user):
    c = Client()
    c.force_login(user)
    return c


def _super():
    return User.objects.create_user(
        email="root@p.tz", password=PW, role=UserRole.SUPER_ADMIN, email_verified=True, phone="255700000001"
    )


def _admin(perms, email="adm@p.tz"):
    role = AdminRole.objects.create(name=f"r-{email}", permissions=perms)
    phone = "2557000%05d" % (abs(hash(email)) % 100000)
    return User.objects.create_user(
        email=email, password=PW, role=UserRole.ADMIN, admin_role=role, email_verified=True, phone=phone
    )


# ── delivery failure is recorded and shown ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_provider_failure_marks_the_row_and_tells_the_user(monkeypatch):
    def boom(phone, text, **kw):
        raise providers.SmsDeliveryError("AstraCall HTTP 401 unauthorized: bad key")

    monkeypatch.setattr(otp, "send_sms", boom)
    user = _user(phone_verified=False)
    resp = _login(user).post(
        "/auth/account/phone/", {"phone": user.phone, "current_password": PW}, follow=True
    )
    body = resp.content.decode()
    assert "could not deliver your code automatically" in body  # told to ask support, on the code page
    assert "Verify your phone" in body
    row = OneTimeCode.objects.get()
    assert row.delivery_status == OtpDelivery.FAILED and "AstraCall HTTP 401" in row.delivery_error
    # The code is NOT voided: support can read it out and the user can still enter it.
    assert row.outcome == OtpOutcome.PENDING and row.is_live and row.status == OtpOutcome.PENDING
    assert row.code_enc
    code = otp.unseal(row)
    c = _login(user)
    assert c.post("/auth/account/verify/phone/", {"code": code}).status_code == 302
    user.refresh_from_db()
    assert user.phone_verified


@pytest.mark.django_db
def test_the_failure_log_never_contains_the_code(monkeypatch, caplog):
    seen = {}

    def boom(phone, text, **kw):
        seen["text"] = text
        raise providers.SmsDeliveryError("down")

    monkeypatch.setattr(otp, "send_sms", boom)
    user = _user()
    with caplog.at_level("WARNING"):
        otp.send_otp(user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    code = re.search(r"code is (\d{6})", seen["text"]).group(1)
    assert "OTP delivery failed" in caplog.text and code not in caplog.text


def test_sms_backend_switch(settings):
    settings.ASTRACALL_API_KEY = "k"
    settings.SMS_BACKEND = "mock"
    assert providers.sms_is_mock()
    settings.SMS_BACKEND = "auto"
    assert not providers.sms_is_mock()
    settings.ASTRACALL_API_KEY = ""
    assert providers.sms_is_mock()
    settings.SMS_BACKEND = "astracall"
    with pytest.raises(providers.SmsDeliveryError):
        providers.send_sms("255712345678", "x")


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def _live(settings):
    settings.SMS_BACKEND = "astracall"
    settings.ASTRACALL_API_KEY = "sk_test_abc"
    settings.ASTRACALL_BASE_URL = "https://astracall.example/api/v1"
    settings.ASTRACALL_SENDER_ID = "IPT MARKET"


def test_astracall_otp_request_shape(settings, monkeypatch):
    _live(settings)
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Resp(202, {"success": True, "data": {"id": "m1", "status": "queued"}})

    monkeypatch.setattr(providers.requests, "post", fake_post)
    out = providers.send_sms("255712345678", "Your code is 123456", kind="otp", idempotency_key="otp-abc")
    assert seen["url"] == "https://astracall.example/api/v1/sms/otp"
    assert seen["json"]["to"] == "+255712345678" and seen["json"]["sender_id"] == "IPT MARKET"
    assert seen["json"]["message"] == "Your code is 123456" and seen["json"]["reference"] == "otp-abc"
    assert seen["headers"]["Authorization"] == "Bearer sk_test_abc"
    assert seen["headers"]["Idempotency-Key"] == "otp-abc"
    assert seen["timeout"] == providers.SMS_TIMEOUT_SECONDS
    assert out["provider"] == "astracall-sms" and out["message_id"] == "m1"
    providers.send_sms("0712345678", "hello")
    assert seen["url"].endswith("/sms/send") and seen["json"]["to"] == "+255712345678"
    assert seen["headers"]["Idempotency-Key"]  # always sent, even when the caller gives none


def test_astracall_errors_become_delivery_errors_without_the_message_text(settings, monkeypatch):
    _live(settings)
    body = {"success": False, "error": {"code": "sender_id_not_approved", "message": "Sender ID is in review"}}
    monkeypatch.setattr(providers.requests, "post", lambda *a, **k: _Resp(422, body))
    with pytest.raises(providers.SmsDeliveryError) as exc:
        providers.send_sms("255712345678", "secret text 654321", kind="otp")
    assert "422" in str(exc.value) and "sender_id_not_approved" in str(exc.value)
    assert "654321" not in str(exc.value)


def test_timeout_and_503_retry_once_with_the_same_idempotency_key(settings, monkeypatch):
    import requests

    _live(settings)
    keys = []
    answers = iter([requests.Timeout(), _Resp(503, {"success": False, "error": {"code": "service_unavailable"}}),
                    _Resp(202, {"success": True, "data": {"id": "m2"}})])

    def fake_post(url, json, headers, timeout):
        keys.append(headers["Idempotency-Key"])
        nxt = next(answers)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(providers.requests, "post", fake_post)
    # attempt 1 times out -> attempt 2 gets 503 -> that is the last attempt, so it is reported as a failure
    with pytest.raises(providers.SmsDeliveryError):
        providers.send_sms("255712345678", "x", idempotency_key="k1")
    assert keys == ["k1", "k1"]
    # 503 first, then accepted
    keys.clear()
    answers = iter([_Resp(503, {"success": False, "error": {"code": "service_unavailable"}}),
                    _Resp(202, {"success": True, "data": {"id": "m3"}})])
    assert providers.send_sms("255712345678", "x", idempotency_key="k2")["message_id"] == "m3"
    assert keys == ["k2", "k2"]


@pytest.mark.django_db
def test_otp_goes_out_as_kind_otp_with_a_stable_key_per_code(monkeypatch):
    seen = {}

    def fake(phone, text, **kw):
        seen.update(kw)
        return {"status": "SENT", "provider": "astracall-sms"}

    monkeypatch.setattr(otp, "send_sms", fake)
    otp.send_otp(_user(), OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    row = OneTimeCode.objects.get()
    assert seen == {"kind": "otp", "idempotency_key": f"otp-{row.id}"}


# ── verifying the number that is already saved ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_unverified_saved_phone_gets_a_verify_button_and_it_works(capsys):
    user = _user(phone="255712345678", phone_verified=False)
    c = _login(user)
    page = c.get("/auth/account/", follow=True).content.decode()
    assert 'id="verify-current-phone"' in page and "Verify this number" in page
    resp = c.post("/auth/account/phone/", {"phone": "255712345678", "current_password": PW})
    assert resp.status_code == 302 and resp.url == "/auth/account/verify/phone/"
    code = re.search(r"code is (\d{6})", capsys.readouterr().out).group(1)
    verify_page = c.get("/auth/account/verify/phone/").content.decode()
    assert "Verify your phone" in verify_page and "Verify your new" not in verify_page
    assert f">{code}<" not in verify_page and "Dev mode" not in verify_page  # the code is never on screen
    assert c.post("/auth/account/verify/phone/", {"code": code}).status_code == 302
    user.refresh_from_db()
    assert user.phone == "255712345678" and user.phone_verified
    assert OneTimeCode.objects.get().outcome == OtpOutcome.VERIFIED
    # Once verified the button is gone.
    assert 'id="verify-current-phone"' not in c.get("/auth/account/", follow=True).content.decode()


@pytest.mark.django_db
def test_verify_current_phone_needs_the_password():
    user = _user(phone_verified=False)
    resp = _login(user).post(
        "/auth/account/phone/", {"phone": user.phone, "current_password": "wrong"}, follow=True
    )
    assert "current password is incorrect" in resp.content.decode()
    assert OneTimeCode.objects.count() == 0


@pytest.mark.django_db
def test_verify_page_never_shows_the_code_even_when_delivery_is_mocked(settings, monkeypatch):
    monkeypatch.setattr(otp, "send_sms", lambda phone, text, **kw: {"status": "SENT", "provider": "mock-sms"})
    user = _user(phone_verified=False)
    c = _login(user)
    c.post("/auth/account/phone/", {"phone": user.phone, "current_password": PW})
    page = c.get("/auth/account/verify/phone/").content.decode()
    code = otp.unseal(OneTimeCode.objects.get())
    assert "Dev mode" not in page and code not in page


# ── outcome bookkeeping ────────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_outcomes_superseded_locked_and_verified():
    user = _user()
    P, C = OtpPurpose.CHANGE_PHONE, OtpChannel.SMS
    otp.send_otp(user, P, C, destination="255787000111")
    first = OneTimeCode.objects.get()
    assert first.delivery_status == OtpDelivery.SENT and first.delivery_provider == "mock-sms"
    assert first.status == OtpOutcome.PENDING and otp.unseal(first)
    otp.send_otp(user, P, C, destination="255787000111")
    first.refresh_from_db()
    assert first.outcome == OtpOutcome.SUPERSEDED and first.code_enc == ""
    for _ in range(otp.OTP_MAX_ATTEMPTS):
        otp.verify_otp(user, P, "000000")
    second = OneTimeCode.objects.order_by("created_at").last()
    assert second.outcome == OtpOutcome.LOCKED and second.code_enc == ""
    otp.send_otp(user, P, C, destination="255787000111")
    code = otp.unseal(OneTimeCode.objects.order_by("created_at").last())
    assert otp.verify_otp(user, P, code) == otp.OK
    done = OneTimeCode.objects.order_by("created_at").last()
    assert done.outcome == OtpOutcome.VERIFIED and done.code_enc == ""


@pytest.mark.django_db
def test_expired_status_is_derived():
    user = _user()
    otp.send_otp(user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    OneTimeCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert OneTimeCode.objects.get().status == OtpOutcome.EXPIRED


# ── admin page ──────────────────────────────────────────────────────────────────────────────────────

URL = "/platform/otp-requests/"


def _seed():
    student = _user("stu@x.tz", phone="255712345678")
    org = _user("org@x.tz", phone="255712345679", role=UserRole.ORGANIZATION)
    otp.send_otp(student, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    otp.send_otp(org, OtpPurpose.RECOVERY, OtpChannel.EMAIL)
    return student, org


@pytest.mark.django_db
def test_admin_page_permissions():
    _seed()
    assert Client().get(URL).status_code == 302  # anonymous -> login
    assert _login(_user("s2@x.tz", phone="255712340000")).get(URL).status_code == 403
    assert _login(_admin({"system_metrics": {"view": True}})).get(URL).status_code == 403
    assert _login(_admin({"user_management": {"view": True}}, "v@p.tz")).get(URL).status_code == 200
    assert _login(_super()).get(URL).status_code == 200


@pytest.mark.django_db
def test_admin_page_lists_who_why_how_where_and_filters():
    _seed()
    c = _login(_super())
    body = c.get(URL).content.decode()
    assert "stu@x.tz" in body and "255787000111" in body and "Change phone" in body
    assert "org@x.tz" in body and "Password recovery" in body

    sms = c.get(URL, {"channel": "SMS"}).content.decode()
    assert "255787000111" in sms and "org@x.tz" not in sms
    purpose = c.get(URL, {"purpose": "RECOVERY"}).content.decode()
    assert "org@x.tz" in purpose and "stu@x.tz" not in purpose
    assert "stu@x.tz" not in c.get(URL, {"q": "org@"}).content.decode()
    assert "stu@x.tz" in c.get(URL, {"q": "255787000111"}).content.decode()
    assert "stu@x.tz" in c.get(URL, {"status": "PENDING"}).content.decode()
    assert "stu@x.tz" not in c.get(URL, {"status": "VERIFIED"}).content.decode()
    assert "stu@x.tz" not in c.get(URL, {"delivery": "FAILED"}).content.decode()
    assert c.get(URL, {"date_from": "not-a-date"}).status_code == 200  # ignored, not a 500


@pytest.mark.django_db
def test_failed_delivery_shows_the_error_and_filters(monkeypatch):
    def boom(phone, text, **kw):
        raise providers.SmsDeliveryError("AstraCall HTTP 401 unauthorized: bad key")

    monkeypatch.setattr(otp, "send_sms", boom)
    user = _user()
    otp.send_otp(user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    c = _login(_super())
    body = c.get(URL, {"delivery": "FAILED"}).content.decode()
    assert "AstraCall HTTP 401" in body and "Failed" in body and "Pending" in body
    assert "u@x.tz" in c.get(URL, {"status": "PENDING"}).content.decode()
    assert 'value="reveal"' in body  # the code is still live, so support can relay it


@pytest.mark.django_db
def test_revoke_voids_the_code_and_is_audited():
    student, _ = _seed()
    row = OneTimeCode.objects.get(user=student)
    c = _login(_super())
    resp = c.post(reverse("platform-otp-action", args=[row.pk]), {"action": "revoke"})
    assert resp.status_code == 302
    row.refresh_from_db()
    assert row.outcome == OtpOutcome.REVOKED and row.code_enc == "" and not row.is_live
    assert otp.verify_otp(student, OtpPurpose.CHANGE_PHONE, "123456") == otp.EXPIRED
    assert AuditLog.objects.filter(action="OTP Revoked", target_id=str(student.pk)).exists()


@pytest.mark.django_db
def test_resend_issues_a_new_code_and_supersedes_the_old(capsys):
    student, _ = _seed()
    old = OneTimeCode.objects.get(user=student)
    c = _login(_super())
    c.post(reverse("platform-otp-action", args=[old.pk]), {"action": "resend"})
    old.refresh_from_db()
    assert old.outcome == OtpOutcome.SUPERSEDED
    new = OneTimeCode.objects.filter(user=student).order_by("created_at").last()
    assert new.pk != old.pk and new.destination == "255787000111" and new.is_live
    assert AuditLog.objects.filter(action="OTP Resent").exists()


@pytest.mark.django_db
def test_action_url_reached_by_get_after_step_up_goes_back_to_the_list():
    student, _ = _seed()
    row = OneTimeCode.objects.get(user=student)
    resp = _login(_super()).get(reverse("platform-otp-action", args=[row.pk]))
    assert resp.status_code == 302 and resp.url == reverse("platform-otp-requests")
    row.refresh_from_db()
    assert row.is_live


@pytest.mark.django_db
def test_resend_only_goes_to_a_contact_that_is_on_the_account_now():
    from apps.core.views_otp import can_resend

    student = _user("stu@x.tz", phone="255712345678", phone_verified=False)
    # Recovery to the old address of an account that has since changed email: refused.
    otp.send_otp(student, OtpPurpose.RECOVERY, OtpChannel.EMAIL)
    row = OneTimeCode.objects.get()
    assert can_resend(row)
    User.objects.filter(pk=student.pk).update(email="new@x.tz")
    row.user.refresh_from_db()
    assert not can_resend(row)
    # Recovery by an unverified phone is refused, a phone change is fine.
    otp.send_otp(student, OtpPurpose.RECOVERY, OtpChannel.SMS)
    sms_row = OneTimeCode.objects.order_by("created_at").last()
    assert not can_resend(sms_row)
    otp.send_otp(student, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    assert can_resend(OneTimeCode.objects.order_by("created_at").last())
    # A resend that is refused sends nothing.
    before = OneTimeCode.objects.count()
    resp = _login(_super()).post(reverse("platform-otp-action", args=[row.pk]), {"action": "resend"})
    assert resp.status_code == 302 and OneTimeCode.objects.count() == before


@pytest.mark.django_db
@pytest.mark.parametrize("role,url", [(UserRole.STUDENT, "/auth/account/"), (UserRole.ORGANIZATION, "/auth/account/")])
def test_verify_form_has_a_password_box_for_students_and_organizations(role, url):
    user = _user(f"{role.lower()}@x.tz", phone="255712345670", phone_verified=False, role=role)
    page = _login(user).get(url, follow=True).content.decode()
    block = page[page.index('id="verify-current-phone"'):]
    block = block[: block.index("</form>")]
    assert 'type="password"' in block and 'name="current_password"' in block
    assert 'name="phone" value="255712345670"' in block


@pytest.mark.django_db
def test_read_only_admin_cannot_act():
    student, _ = _seed()
    row = OneTimeCode.objects.get(user=student)
    viewer = _admin({"user_management": {"view": True}})
    c = _login(viewer)
    assert 'name="action"' not in c.get(URL).content.decode()  # no buttons without edit
    assert c.post(reverse("platform-otp-action", args=[row.pk]), {"action": "revoke"}).status_code == 403
    row.refresh_from_db()
    assert row.is_live


@pytest.mark.django_db
def test_reveal_shows_the_live_code_and_is_audited(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        otp, "send_sms",
        lambda phone, text, **kw: sent.update(text=text) or {"status": "SENT", "provider": "astracall-sms"},
    )
    student = _user("stu@x.tz")
    otp.send_otp(student, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    real_code = re.search(r"code is (\d{6})", sent["text"]).group(1)
    row = OneTimeCode.objects.get()
    assert row.code_enc and real_code not in row.code_enc  # encrypted at rest, never plain
    c = _login(_super())
    assert real_code not in c.get(URL).content.decode()  # not in the list either
    page = c.post(reverse("platform-otp-action", args=[row.pk]), {"action": "reveal"})
    assert page.status_code == 200 and real_code in page.content.decode()
    assert "no-store" in page["Cache-Control"]
    log = AuditLog.objects.get(action="OTP Code Revealed")
    assert real_code not in str(log.context) and log.target_id == str(student.pk)
    # Once used it is gone for good.
    assert otp.verify_otp(student, OtpPurpose.CHANGE_PHONE, real_code) == otp.OK
    again = c.post(reverse("platform-otp-action", args=[row.pk]), {"action": "reveal"})
    assert again.status_code == 302 and real_code not in c.get(URL).content.decode()


@pytest.mark.django_db
def test_any_admin_with_edit_can_view_but_a_viewer_cannot():
    student = _user("stu@x.tz")
    otp.send_otp(student, OtpPurpose.RECOVERY, OtpChannel.SMS)
    row = OneTimeCode.objects.get()
    url = reverse("platform-otp-action", args=[row.pk])
    editor = _admin({"user_management": {"view": True, "edit": True}}, "ed@p.tz")
    assert _login(editor).post(url, {"action": "reveal"}).status_code == 200  # recovery codes included
    viewer = _admin({"user_management": {"view": True}}, "vw@p.tz")
    assert _login(viewer).post(url, {"action": "reveal"}).status_code == 403
    assert 'value="reveal"' not in _login(viewer).get(URL).content.decode()


@pytest.mark.django_db
def test_codes_of_administrator_accounts_are_never_shown():
    boss = _super()
    otp.send_otp(boss, OtpPurpose.RECOVERY, OtpChannel.SMS)
    row = OneTimeCode.objects.get()
    editor = _admin({"user_management": {"view": True, "edit": True}}, "ed@p.tz")
    c = _login(editor)
    assert c.post(reverse("platform-otp-action", args=[row.pk]), {"action": "reveal"}).status_code == 302
    assert not AuditLog.objects.filter(action="OTP Code Revealed").exists()
    assert 'value="reveal"' not in c.get(URL).content.decode()


@pytest.mark.django_db
def test_nothing_is_stored_when_the_switch_is_off(settings):
    settings.OTP_ADMIN_CAN_VIEW_CODES = False
    otp.send_otp(_user(), OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    row = OneTimeCode.objects.get()
    assert row.code_enc == "" and otp.unseal(row) is None
    c = _login(_super())
    assert c.post(reverse("platform-otp-action", args=[row.pk]), {"action": "reveal"}).status_code == 302
    assert 'value="reveal"' not in c.get(URL).content.decode()


@pytest.mark.django_db
def test_expired_codes_are_wiped_when_the_page_loads():
    _seed()
    OneTimeCode.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    _login(_super()).get(URL)
    assert not OneTimeCode.objects.exclude(code_enc="").exists()


# ── failed delivery: what is kept, what is voided ────────────────────────────────────────────

def _failing(monkeypatch):
    def boom(phone, text, **kw):
        raise providers.SmsDeliveryError("AstraCall HTTP 503 service_unavailable")

    monkeypatch.setattr(otp, "send_sms", boom)


@pytest.mark.django_db
def test_failed_recovery_code_can_still_be_used_after_support_relays_it(monkeypatch):
    from apps.accounts.views_otp import RESET_CHANNEL_KEY, RESET_EMAIL_KEY

    _failing(monkeypatch)
    user = _user("rec@x.tz")
    c = Client()
    session = c.session
    session[RESET_EMAIL_KEY], session[RESET_CHANNEL_KEY] = user.email, "SMS"
    session.save()
    assert c.post("/auth/reset-password/method/", {"channel": "SMS"}).status_code == 302
    row = OneTimeCode.objects.get(user=user, purpose=OtpPurpose.RECOVERY)
    assert row.delivery_status == OtpDelivery.FAILED and row.is_live
    code = otp.unseal(row)
    resp = c.post("/auth/reset-password/verify/", {"code": code})
    assert resp.status_code == 302 and resp.url == reverse("reset-password")


@pytest.mark.django_db
def test_administrator_codes_are_not_stored_and_a_failed_send_is_voided(monkeypatch):
    _failing(monkeypatch)
    boss = _super()
    result = otp.send_otp(boss, OtpPurpose.RECOVERY, OtpChannel.SMS)
    row = OneTimeCode.objects.get()
    assert row.code_enc == "" and not result.sent and not result.code_kept
    assert row.outcome == OtpOutcome.UNDELIVERED and not row.is_live
    assert row.status == OtpOutcome.UNDELIVERED
    # Delivered normally it is also not stored.
    OneTimeCode.objects.all().delete()
    monkeypatch.undo()
    otp.send_otp(boss, OtpPurpose.RECOVERY, OtpChannel.SMS)
    assert OneTimeCode.objects.get().code_enc == ""


@pytest.mark.django_db
def test_failed_send_with_the_switch_off_is_voided(monkeypatch, settings):
    settings.OTP_ADMIN_CAN_VIEW_CODES = False
    _failing(monkeypatch)
    user = _user()
    result = otp.send_otp(user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    row = OneTimeCode.objects.get()
    assert not result.sent and not result.code_kept
    assert row.outcome == OtpOutcome.UNDELIVERED and not row.is_live
    resp = _login(user).post("/auth/account/phone/", {"phone": "255787000111", "current_password": PW}, follow=True)
    assert "We could not send a code" in resp.content.decode()  # the ordinary message, no "ask support"


@pytest.mark.django_db
def test_expired_ciphertext_is_wiped_by_the_task_and_on_verify():
    from apps.accounts.tasks import wipe_expired_otp_codes

    user = _user()
    otp.send_otp(user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    OneTimeCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert OneTimeCode.objects.exclude(code_enc="").exists()
    assert wipe_expired_otp_codes() == 1
    assert not OneTimeCode.objects.exclude(code_enc="").exists()
    # A verify attempt on an expired code also clears it.
    otp.send_otp(user, OtpPurpose.CHANGE_PHONE, OtpChannel.SMS, destination="255787000111")
    OneTimeCode.objects.filter(consumed_at__isnull=True).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert otp.verify_otp(user, OtpPurpose.CHANGE_PHONE, "123456") == otp.EXPIRED
    assert not OneTimeCode.objects.exclude(code_enc="").exists()
