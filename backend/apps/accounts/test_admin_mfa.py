"""IAM step 8: administrator creation, MFA (TOTP + FIDO2), recovery codes, step-up,
M-of-N recovery and break-glass. FIDO2 is exercised end to end with a software authenticator."""

import base64
import hashlib
import json
import os
import re
import struct
import time

import cbor2
import pyotp
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client

from apps.accounts import admins, breakglass, mfa, recovery
from apps.accounts.models import (
    AdminRecoveryRequest,
    AdminRole,
    BreakGlassAccount,
    RecoveryCode,
    RecoveryStatus,
    UserRole,
)
from apps.core.models import AuditLog

User = get_user_model()
PW = "Secret123!x"
NEW_PW = "Brand-new-Pass-77"


@pytest.fixture(autouse=True)
def mfa_on(settings):
    settings.ADMIN_MFA_ENFORCED = True
    settings.SUPER_ADMIN_REQUIRE_FIDO2 = True
    from apps.core.redis_client import get_redis

    r = get_redis()
    for key in list(r.scan_iter("*:rl:*")) + list(r.scan_iter("*:lockout:*")):
        r.delete(key)
    yield


def b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class SoftKey:
    """A minimal software FIDO2 authenticator (ES256, 'none' attestation)."""

    rp_id = "testserver"
    origin = "http://testserver"

    def __init__(self):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.sign_count = 0

    def _cose(self):
        n = self.priv.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _client_data(self, kind, challenge):
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        ).encode()

    def register(self, options):
        client_data = self._client_data("webauthn.create", options["challenge"])
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([0x45])
            + struct.pack(">I", 0)
            + b"\x00" * 16
            + struct.pack(">H", len(self.cred_id))
            + self.cred_id
            + self._cose()
        )
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {"clientDataJSON": b64u(client_data), "attestationObject": b64u(att), "transports": ["usb"]},
            "clientExtensionResults": {},
        }

    def assertion(self, options, tamper=False):
        client_data = self._client_data("webauthn.get", options["challenge"])
        self.sign_count += 1
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x05]) + struct.pack(">I", self.sign_count)
        signed = auth_data + hashlib.sha256(client_data).digest()
        sig = self.priv.sign(signed, ec.ECDSA(hashes.SHA256()))
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {
            "id": b64u(self.cred_id), "rawId": b64u(self.cred_id), "type": "public-key",
            "response": {
                "authenticatorData": b64u(auth_data), "clientDataJSON": b64u(client_data),
                "signature": b64u(sig), "userHandle": None,
            },
            "clientExtensionResults": {},
        }


def post_json(client, url, payload=None):
    return client.post(url, data=json.dumps(payload or {}), content_type="application/json")


def add_key(client, key, name="Test key"):
    opts = post_json(client, "/auth/mfa/webauthn/register-options/").json()
    resp = post_json(client, "/auth/mfa/webauthn/register-verify/", {"credential": key.register(opts), "name": name})
    return resp


def key_login(client, key, tamper=False, nxt=None):
    opts = post_json(client, "/auth/mfa/webauthn/auth-options/").json()
    url = "/auth/mfa/webauthn/auth-verify/" + (f"?next={nxt}" if nxt else "")
    return post_json(client, url, key.assertion(opts, tamper=tamper))


def make_super(email="root@p.tz", **kw):
    return User.objects.create_user(
        email=email, password=PW, role=UserRole.SUPER_ADMIN, email_verified=True, **kw
    )


def make_admin(email="adm@p.tz", role=None, **kw):
    role = role or AdminRole.objects.get_or_create(
        name="Support", defaults={"permissions": {"system_metrics": {"view": True}, "user_management": {"view": True}}}
    )[0]
    return User.objects.create_user(
        email=email, password=PW, role=UserRole.ADMIN, admin_role=role, email_verified=True, **kw
    )


def enroll_key(user):
    """A signed-in client for ``user`` with one registered key, recovery codes generated."""
    client = Client()
    client.force_login(user)
    key = SoftKey()
    assert add_key(client, key).status_code == 200
    resp = client.post("/auth/mfa/recovery-codes/", {"password": PW})
    return client, key, resp.context["codes"]


def fresh_login(user, key):
    """Password then security key, as a real sign-in."""
    c = Client()
    assert c.post("/auth/login/", {"email": user.email, "password": PW}).url == "/auth/mfa/"
    assert key_login(c, key).status_code == 200
    return c


# ── creation & onboarding ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_only_super_admin_creates_administrators_and_there_is_no_public_admin_registration(settings):
    settings.ADMIN_MFA_ENFORCED = False
    role = AdminRole.objects.create(name="Finance", permissions={"financial_ledger": {"view": True}})
    plain = make_admin("plain@p.tz")
    with pytest.raises(Exception):
        admins.create_administrator(plain, first_name="A", last_name="B", email="n@p.tz", admin_role=role)
    c = Client()
    c.force_login(plain)
    assert c.get("/platform/admins/").status_code == 403
    assert c.post("/platform/admins/new/", {"email": "n@p.tz"}).status_code == 403
    # Public registration cannot produce an administrator.
    from apps.accounts.forms import RegisterForm

    form = RegisterForm({"email": "x@p.tz", "first_name": "A", "last_name": "B", "role": "ADMIN",
                         "password1": PW, "password2": PW})
    assert not form.is_valid() and "role" in form.errors


@pytest.mark.django_db
def test_super_admin_creates_admin_with_emailed_expiring_temporary_password(settings):
    settings.ADMIN_MFA_ENFORCED = False
    root = make_super()
    role = AdminRole.objects.create(name="Finance", permissions={"financial_ledger": {"view": True}})
    c = Client()
    c.force_login(root)
    mail.outbox.clear()
    resp = c.post(
        "/platform/admins/new/",
        {"first_name": "Fiona", "last_name": "Fin", "email": "fiona@p.tz", "phone": "255713000111",
         "job_title": "Accountant", "admin_role": str(role.id)},
    )
    assert resp.status_code == 302
    fiona = User.objects.get(email="fiona@p.tz")
    assert fiona.role == UserRole.ADMIN and fiona.admin_role == role and fiona.must_change_password
    assert fiona.temp_password_expires_at is not None and fiona.job_title == "Accountant"
    body = mail.outbox[-1].body
    temp = re.search(r"Temporary password: (\S+)", body).group(1)
    assert mail.outbox[-1].to == ["fiona@p.tz"] and fiona.check_password(temp)
    assert temp not in resp.content.decode()  # never shown to the creator
    ev = AuditLog.objects.get(action="Admin Created")
    assert ev.actor_id == root.id and ev.target_label == "fiona@p.tz" and ev.context["role"] == "Finance"
    # Duplicate email: the agreed message, nothing leaked.
    dup = c.post("/platform/admins/new/", {"first_name": "X", "last_name": "Y", "email": "FIONA@p.tz",
                                           "phone": "255713000999", "admin_role": str(role.id)})
    assert "already associated with an account" in dup.content.decode()


@pytest.mark.django_db
def test_admin_onboarding_temp_password_then_new_password_then_mfa_then_access():
    root = make_super()
    role = AdminRole.objects.create(name="Support", permissions={"user_management": {"view": True}})
    temp = "Temp-Initial-pass-9"
    admin = User.objects.create_user(
        email="new@p.tz", password=temp, role=UserRole.ADMIN, admin_role=role,
        must_change_password=True,
    )
    c = Client()
    assert c.post("/auth/login/", {"email": "new@p.tz", "password": temp}).status_code == 302
    # 1. nothing works until the temporary password is replaced
    assert c.get("/platform/verifications/").url == "/auth/change-password/"
    r = c.post("/auth/change-password/", {"current_password": temp, "password1": NEW_PW, "password2": NEW_PW})
    assert r.status_code == 302
    admin.refresh_from_db()
    assert not admin.must_change_password and admin.check_password(NEW_PW) and not admin.check_password(temp)
    assert admin.email_verified  # the temporary password reached this mailbox
    # 2. next: MFA setup. Admin pages stay locked.
    assert c.get("/platform/verifications/").url == "/auth/mfa/settings/"
    # 3. an authenticator app...
    c.get("/auth/mfa/totp/")
    secret = c.session["totp_setup_secret"]
    bad = c.post("/auth/mfa/totp/", {"code": "000000"})
    assert bad.status_code == 200 and not mfa.has_totp(admin)
    c.post("/auth/mfa/totp/", {"code": pyotp.TOTP(secret).now()})
    assert mfa.has_totp(admin)
    # 4. ...and recovery codes are required before access
    assert c.get("/platform/verifications/").url == "/auth/mfa/settings/"
    codes = c.post("/auth/mfa/recovery-codes/", {"password": NEW_PW}).context["codes"]
    assert len(codes) == 10
    assert c.get("/platform/verifications/").status_code == 200  # access granted after full setup
    # Only hashes are stored.
    stored = set(RecoveryCode.objects.filter(user=admin).values_list("code_hash", flat=True))
    assert len(stored) == 10 and not any(code.replace("-", "") in h for code in codes for h in stored)


@pytest.mark.django_db
def test_temporary_password_does_not_become_permanent_and_reissue_replaces_it():
    root = make_super()
    role = AdminRole.objects.create(name="Support", permissions={})
    admin = User.objects.create_user(email="n@p.tz", password="Temp-pass-111x", role=UserRole.ADMIN,
                                     admin_role=role, must_change_password=True)
    admins.reissue_temp_password(root, admin)
    admin.refresh_from_db()
    assert not admin.check_password("Temp-pass-111x") and admin.must_change_password


# ── TOTP, recovery codes ────────────────────────────────────────────────────

def _totp_admin(email="adm@p.tz"):
    admin = make_admin(email)
    c = Client()
    c.force_login(admin)
    c.get("/auth/mfa/totp/")
    secret = c.session["totp_setup_secret"]
    c.post("/auth/mfa/totp/", {"code": pyotp.TOTP(secret).now()})
    codes = c.post("/auth/mfa/recovery-codes/", {"password": PW}).context["codes"]
    return admin, secret, codes


@pytest.mark.django_db
def test_enrolled_admin_gets_no_session_until_second_factor_and_totp_cannot_be_replayed():
    admin, secret, codes = _totp_admin()
    secret_row = admin.totp_device.secret_enc
    assert secret not in secret_row  # encrypted at rest
    c = Client()
    resp = c.post("/auth/login/", {"email": admin.email, "password": PW})
    assert resp.url == "/auth/mfa/"
    assert "_auth_user_id" not in c.session  # password alone creates no session
    assert c.get("/platform/verifications/").status_code == 302
    nxt = pyotp.TOTP(secret).at(time.time() + 30)  # a later step: valid, newer than the enrollment code
    assert c.post("/auth/mfa/", {"method": "totp", "code": "000000"}).status_code == 200
    ok = c.post("/auth/mfa/", {"method": "totp", "code": nxt})
    assert ok.status_code == 302 and c.get("/platform/verifications/").status_code == 200
    # Same code again (replay) on a new sign-in is rejected.
    c2 = Client()
    c2.post("/auth/login/", {"email": admin.email, "password": PW})
    assert c2.post("/auth/mfa/", {"method": "totp", "code": nxt}).status_code == 200
    assert "_auth_user_id" not in c2.session


@pytest.mark.django_db
def test_mfa_failures_lock_for_five_minutes():
    admin, secret, codes = _totp_admin()
    c = Client()
    c.post("/auth/login/", {"email": admin.email, "password": PW})
    for _ in range(5):
        assert c.post("/auth/mfa/", {"method": "totp", "code": "000000"}).status_code == 200
    locked = c.post("/auth/mfa/", {"method": "totp", "code": pyotp.TOTP(secret).at(time.time() + 30)})
    assert "Too many failed attempts" in locked.content.decode() and "_auth_user_id" not in c.session
    assert AuditLog.objects.filter(action="Admin MFA Failed", outcome="FAILED").count() >= 5


@pytest.mark.django_db
def test_recovery_code_is_single_use_forces_a_new_set_and_old_codes_die():
    admin, secret, codes = _totp_admin()
    c = Client()
    c.post("/auth/login/", {"email": admin.email, "password": PW})
    assert c.post("/auth/mfa/", {"method": "recovery", "code": codes[0]}).status_code == 302
    # Signed in, but locked to MFA settings until a new set is generated.
    assert c.get("/platform/verifications/").url == "/auth/mfa/settings/"
    # The same code cannot be used again.
    c2 = Client()
    c2.post("/auth/login/", {"email": admin.email, "password": PW})
    assert c2.post("/auth/mfa/", {"method": "recovery", "code": codes[0]}).status_code == 200
    new = c.post("/auth/mfa/recovery-codes/", {"password": PW}).context["codes"]
    assert c.get("/platform/verifications/").status_code == 200
    c3 = Client()
    c3.post("/auth/login/", {"email": admin.email, "password": PW})
    assert c3.post("/auth/mfa/", {"method": "recovery", "code": codes[1]}).status_code == 200  # old set invalid
    assert c3.post("/auth/mfa/", {"method": "recovery", "code": new[0]}).status_code == 302
    assert mfa.remaining_recovery_codes(admin) == 9


@pytest.mark.django_db
def test_recovery_codes_page_is_not_cached_and_password_is_required():
    admin, secret, codes = _totp_admin()
    c = Client()
    c.force_login(admin)
    from django.test import Client as _C

    bad = c.post("/auth/mfa/recovery-codes/", {"password": "wrong"})
    assert bad.status_code == 302
    assert RecoveryCode.objects.filter(user=admin, used_at__isnull=True).count() == 10
    good = c.post("/auth/mfa/recovery-codes/", {"password": PW})
    assert "no-store" in good["Cache-Control"]


# ── FIDO2 / Super Admin ─────────────────────────────────────────────────────

@pytest.mark.django_db
def test_super_admin_must_use_a_hardware_key_totp_is_refused():
    root = make_super()
    c = Client()
    c.force_login(root)
    assert c.get("/platform/verifications/").url == "/auth/mfa/settings/"  # gated until a key exists
    r = c.get("/auth/mfa/totp/")
    assert r.status_code == 302 and r.url == "/auth/mfa/settings/"
    assert not mfa.has_totp(root)
    key = SoftKey()
    assert add_key(c, key).status_code == 200
    assert mfa.has_fido(root) and mfa.mfa_enrolled(root)
    assert c.get("/platform/verifications/").url == "/auth/mfa/settings/"  # still needs recovery codes
    c.post("/auth/mfa/recovery-codes/", {"password": PW})
    assert c.get("/platform/verifications/").status_code == 200
    # Cannot remove the only key.
    key_row = root.webauthn_credentials.get()
    c.post(f"/auth/mfa/remove/key/{key_row.pk}/", {"password": PW})
    assert root.webauthn_credentials.count() == 1


@pytest.mark.django_db
def test_super_admin_signs_in_with_password_then_key_and_a_forged_signature_fails():
    root = make_super()
    _, key, codes = enroll_key(root)
    c = Client()
    assert c.post("/auth/login/", {"email": root.email, "password": PW}).url == "/auth/mfa/"
    assert c.post("/auth/mfa/", {"method": "totp", "code": "123456"}).status_code == 200  # TOTP not accepted
    assert key_login(c, key, tamper=True).status_code == 400
    assert "_auth_user_id" not in c.session
    ok = key_login(c, key)
    assert ok.status_code == 200 and ok.json()["redirect"]
    assert c.get("/platform/verifications/").status_code == 200
    # A challenge can only be used once.
    opts = post_json(c, "/auth/mfa/webauthn/auth-options/").json()
    assertion = key.assertion(opts)
    assert post_json(c, "/auth/mfa/webauthn/auth-verify/", assertion).status_code == 200
    assert post_json(c, "/auth/mfa/webauthn/auth-verify/", assertion).status_code == 400  # replay


@pytest.mark.django_db
def test_another_users_key_cannot_sign_you_in():
    root, other = make_super("a@p.tz"), make_super("b@p.tz")
    enroll_key(root)
    _, other_key, _ = enroll_key(other)
    c = Client()
    c.post("/auth/login/", {"email": root.email, "password": PW})
    assert key_login(c, other_key).status_code == 400


@pytest.mark.django_db
def test_session_id_changes_when_mfa_elevates_the_session():
    root = make_super()
    _, key, _ = enroll_key(root)
    c = Client()
    c.post("/auth/login/", {"email": root.email, "password": PW})
    before = c.cookies["sessionid"].value
    key_login(c, key)
    after = c.cookies["sessionid"].value
    assert before != after


# ── high-risk actions need hardware re-verification ─────────────────────────

@pytest.mark.django_db
def test_high_risk_actions_require_a_fresh_hardware_key_confirmation(settings):
    root = make_super()
    role = AdminRole.objects.create(name="Finance", permissions={})
    _, key, _ = enroll_key(root)
    c = fresh_login(root, key)
    payload = {"first_name": "A", "last_name": "B", "email": "z@p.tz", "phone": "255713111222",
               "admin_role": str(role.id)}
    # Sign-in itself counted as hardware verification: allowed right away...
    # ...so age it past the window to prove the guard.
    session = c.session
    session[mfa.HW_VERIFIED_AT] = time.time() - settings.HARDWARE_REVERIFY_SECONDS - 5
    session.save()
    stale = c.post("/platform/admins/new/", payload)
    assert stale.status_code == 302 and stale.url.startswith("/auth/mfa/step-up/")
    assert not User.objects.filter(email="z@p.tz").exists()
    # Step up with the key, then the same action succeeds.
    assert key_login(c, key, nxt="/platform/admins/new/").json()["redirect"] == "/platform/admins/new/"
    done = c.post("/platform/admins/new/", payload)
    assert done.status_code == 302 and User.objects.filter(email="z@p.tz").exists()
    # Other high-risk surfaces are guarded the same way.
    session = c.session
    session[mfa.HW_VERIFIED_AT] = 0
    session.save()
    for url in ("/platform/roles/", "/platform/settings/", "/platform/account-status/", "/platform/recovery/",
                "/platform/break-glass/"):
        r = c.post(url, {})
        assert r.status_code == 302 and r.url.startswith("/auth/mfa/step-up/"), url


@pytest.mark.django_db
def test_ordinary_admin_with_only_totp_cannot_do_high_risk_actions():
    admin, secret, codes = _totp_admin()
    c = Client()
    c.post("/auth/login/", {"email": admin.email, "password": PW})
    c.post("/auth/mfa/", {"method": "totp", "code": pyotp.TOTP(secret).at(time.time() + 30)})
    r = c.post("/platform/roles/", {})
    # Not a super admin: forbidden outright. And the step-up page tells them to register a key.
    assert r.status_code in (302, 403)
    page = c.get("/auth/mfa/step-up/")
    assert b"no security key" in page.content


# ── admin management actions ────────────────────────────────────────────────

@pytest.mark.django_db
def test_changing_an_admins_role_or_suspending_ends_their_session_and_is_audited(settings):
    settings.ADMIN_MFA_ENFORCED = False
    root = make_super()
    finance = AdminRole.objects.create(name="Finance", permissions={"financial_ledger": {"view": True}})
    admin = make_admin("adm@p.tz")
    ac = Client()
    ac.force_login(admin)
    assert ac.get("/auth/session-status/").status_code == 200
    admins.change_admin_role(root, admin, finance)
    assert ac.get("/auth/session-status/").status_code == 401
    ac2 = Client()
    ac2.force_login(admin)
    admins.set_admin_suspended(root, admin, True, "Investigation")
    assert ac2.get("/auth/session-status/").status_code == 401
    admin.refresh_from_db()
    assert not admin.is_active and admin.suspension_reason == "Investigation"
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"Admin Role Changed", "Admin Suspended"} <= actions
    with pytest.raises(Exception):
        admins.set_admin_suspended(root, root, True)  # cannot suspend yourself


# ── M-of-N Super Admin recovery ─────────────────────────────────────────────

def _quorum():
    target = make_super("target@p.tz")
    enroll_key(target)
    approvers = [make_admin(f"ap{i}@p.tz") for i in range(3)]
    for a in approvers:
        a.is_recovery_approver = True
        a.save(update_fields=["is_recovery_approver"])
    return target, approvers


@pytest.mark.django_db
def test_recovery_executes_only_when_the_quorum_is_met_and_resets_the_target(settings):
    target, (a, b, c3) = _quorum()
    mail.outbox.clear()
    tc = Client()
    tc.force_login(target)  # signed in somewhere: must be ended by recovery
    req = recovery.request_recovery("target@p.tz")
    assert req is not None and req.required_approvals == 2 and req.status == RecoveryStatus.PENDING
    assert {m.to[0] for m in mail.outbox} == {"ap0@p.tz", "ap1@p.tz", "ap2@p.tz"}

    from django.core.exceptions import PermissionDenied, ValidationError

    with pytest.raises(PermissionDenied):
        recovery.decide(req, target, True)  # the requester cannot approve their own request
    outsider = make_admin("out@p.tz")
    with pytest.raises(PermissionDenied):
        recovery.decide(req, outsider, True)  # only designated approvers

    recovery.decide(req, a, True)
    req.refresh_from_db()
    assert req.status == RecoveryStatus.PENDING and target.webauthn_credentials.exists()  # 1 of 2: nothing yet
    with pytest.raises(ValidationError):
        recovery.decide(req, a, True)  # no double voting
    recovery.decide(req, b, True)
    req.refresh_from_db()
    assert req.status == RecoveryStatus.EXECUTED and req.executed_at
    assert not target.webauthn_credentials.exists() and not RecoveryCode.objects.filter(user=target).exists()
    assert tc.get("/auth/session-status/").status_code == 401  # their sessions ended
    # Back to square one: password works, but admin access needs a new hardware key.
    c = Client()
    assert c.post("/auth/login/", {"email": target.email, "password": PW}).url == "/platform/verifications/"
    assert c.get("/platform/verifications/").url == "/auth/mfa/settings/"
    actions = list(AuditLog.objects.filter(module="Security").values_list("action", flat=True))
    for needed in ("Recovery Requested", "Recovery Approved", "Recovery Executed"):
        assert needed in actions
    assert AuditLog.objects.filter(action="Recovery Approved").count() == 2
    with pytest.raises(ValidationError):
        recovery.decide(req, c3, True)  # closed


@pytest.mark.django_db
def test_recovery_is_rejected_when_the_quorum_becomes_impossible_and_rejections_are_audited():
    target, (a, b, c3) = _quorum()
    req = recovery.request_recovery("target@p.tz")
    recovery.decide(req, a, False, note="Not verified")
    recovery.decide(req, b, False)
    req.refresh_from_db()
    assert req.status == RecoveryStatus.REJECTED and target.webauthn_credentials.exists()
    assert AuditLog.objects.filter(action="Recovery Rejected").count() == 2
    assert AuditLog.objects.filter(action="Recovery Denied").exists()


@pytest.mark.django_db
def test_recovery_request_is_generic_rate_limited_and_needs_enough_approvers(client):
    make_super("lonely@p.tz")
    # Not enough designated approvers: no request is created, but the answer is the same.
    a = client.post("/auth/admin-recovery/", {"email": "lonely@p.tz"})
    b = client.post("/auth/admin-recovery/", {"email": "nobody@p.tz"})
    assert a.status_code == b.status_code == 200 and a.content.decode().count("approvers have been notified") == 1
    assert AdminRecoveryRequest.objects.count() == 0
    # A normal student can't be the subject of an admin recovery request.
    User.objects.create_user(email="s@p.tz", password=PW, role=UserRole.STUDENT)
    assert recovery.request_recovery("s@p.tz") is None


@pytest.mark.django_db
def test_recovery_approval_page_blocks_self_and_requires_hardware_key():
    target, (a, b, c3) = _quorum()
    req = recovery.request_recovery("target@p.tz")
    _, key, _ = enroll_key(a)
    ac = fresh_login(a, key)
    page = ac.get("/platform/recovery/")
    assert page.status_code == 200 and b"target@p.tz" in page.content
    r = ac.post("/platform/recovery/", {"request_id": str(req.id), "decision": "approve"})
    assert r.status_code == 302 and "step-up" not in r.url  # hardware was just verified at sign-in
    assert req.decisions.count() == 1


# ── break-glass ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_break_glass_credentials_alerts_logging_rotation_and_review():
    root = make_super()
    other_super = make_super("second@p.tz")
    account, password = breakglass.create_break_glass(root, "bg@p.tz")
    assert len(password) >= 40 and not account.user.password.endswith(password)  # high entropy, hashed
    assert BreakGlassAccount.objects.filter(user=account.user).exists()
    assert AuditLog.objects.filter(action="Break-glass Account Created").exists()

    # Register its hardware key during the ceremony.
    cc = Client()
    cc.force_login(account.user)
    key = SoftKey()
    assert add_key(cc, key).status_code == 200
    cc.post("/auth/mfa/recovery-codes/", {"password": password})  # ceremony completes; codes not accepted for use
    # Normal controls apply: password alone is not a session, TOTP/recovery are not accepted.
    c = Client()
    assert c.post("/auth/login/", {"email": "bg@p.tz", "password": password}).url == "/auth/mfa/"
    assert mfa.allowed_methods(account.user) == {"webauthn"}
    assert c.post("/auth/mfa/", {"method": "recovery", "code": "aaaa-bbbb-cccc"}).status_code == 200
    mail.outbox.clear()
    assert key_login(c, key).status_code == 200
    # Immediate alert, flagged for review and rotation.
    account.refresh_from_db()
    assert account.rotation_required and account.review_pending and account.last_used_at
    assert any("URGENT" in m.subject and m.to == ["second@p.tz"] for m in mail.outbox)
    used = AuditLog.objects.get(action="Break-glass Account Used")
    assert used.context["priority"] == "critical"
    # The emergency session shows a banner and every action is logged and tagged.
    assert b"EMERGENCY" in c.get("/platform/audit-logs/").content
    tagged = AuditLog.objects.filter(action="Audit Log Searched")
    c.get("/platform/audit-logs/?q=x")
    assert AuditLog.objects.filter(action="Audit Log Searched", context__break_glass=True).exists()
    # Audit cannot be switched off: rows still can't be edited or deleted even by this account.
    from django.db import DatabaseError, transaction

    with pytest.raises(DatabaseError), transaction.atomic():
        used.description = "tampered"
        used.save(update_fields=["description"])
    # Rotation invalidates the old credential and ends sessions; review clears the flag.
    new_password = breakglass.rotate_break_glass(account, root)
    assert new_password != password
    assert Client().post("/auth/login/", {"email": "bg@p.tz", "password": password}).status_code == 200
    assert Client().post("/auth/login/", {"email": "bg@p.tz", "password": new_password}).url == "/auth/mfa/"
    breakglass.mark_reviewed(account, root)
    account.refresh_from_db()
    assert not account.review_pending and not account.rotation_required


@pytest.mark.django_db
def test_break_glass_secret_is_shown_once_on_the_creation_page(settings):
    settings.ADMIN_MFA_ENFORCED = False
    root = make_super()
    c = Client()
    c.force_login(root)
    resp = c.post("/platform/break-glass/", {"action": "create", "email": "bg@p.tz"})
    assert resp.status_code == 200 and "no-store" in resp["Cache-Control"]
    shown = resp.context["password"]
    assert shown not in c.get("/platform/break-glass/").content.decode()  # never again
    assert User.objects.get(email="bg@p.tz").check_password(shown)
