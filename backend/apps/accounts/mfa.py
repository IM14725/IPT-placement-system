"""Administrator multi-factor authentication.

* Ordinary administrators: an authenticator app (TOTP) or a FIDO2/WebAuthn security key.
* Super Admins (and break-glass accounts): a hardware key is mandatory.
* High-risk actions need a hardware-key re-verification within ``HARDWARE_REVERIFY_SECONDS``.
* Offline one-use recovery codes are shown once and only their keyed hash is stored.
"""

import base64
import hashlib
import hmac
import secrets
import time

import pyotp
import segno
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, options_to_json_dict
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from apps.accounts.models import RecoveryCode, TotpDevice, User, UserRole, WebAuthnCredential

TOTP_ISSUER = "IPT Marketplace"
RECOVERY_CODE_COUNT = 10

# Session flags
MFA_OK = "mfa_ok"
MFA_PENDING = "mfa_pending_user"
MFA_PENDING_AT = "mfa_pending_at"
HW_VERIFIED_AT = "hw_verified_at"
RECOVERY_USED = "mfa_recovery_used"
REG_CHALLENGE = "webauthn_reg_challenge"
AUTH_CHALLENGE = "webauthn_auth_challenge"
PENDING_TTL_SECONDS = 300


# ── policy ──────────────────────────────────────────────────────────────────

def is_admin_user(user):
    return bool(
        user is not None
        and user.is_authenticated
        and (user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN) or user.is_superuser)
    )


def enforced():
    return bool(getattr(settings, "ADMIN_MFA_ENFORCED", True))


def is_break_glass(user):
    return hasattr(user, "break_glass")


def requires_fido(user):
    """Super Admins and break-glass accounts must use a hardware key."""
    is_super = user.role == UserRole.SUPER_ADMIN or user.is_superuser
    return bool(is_super and (settings.SUPER_ADMIN_REQUIRE_FIDO2 or is_break_glass(user)))


def has_totp(user):
    return TotpDevice.objects.filter(user=user, confirmed_at__isnull=False).exists()


def has_fido(user):
    return WebAuthnCredential.objects.filter(user=user).exists()


def mfa_enrolled(user):
    if requires_fido(user):
        return has_fido(user)
    return has_fido(user) or has_totp(user)


def allowed_methods(user):
    """Factors that may complete a sign-in for this admin."""
    methods = set()
    if has_fido(user):
        methods.add("webauthn")
    if has_totp(user) and not requires_fido(user):
        methods.add("totp")
    if not is_break_glass(user):
        methods.add("recovery")
    return methods


# ── crypto helpers ──────────────────────────────────────────────────────────

def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def _code_hash(code):
    normalized = "".join(ch for ch in code.lower() if ch.isalnum())
    return hmac.new(settings.SECRET_KEY.encode(), f"recovery:{normalized}".encode(), hashlib.sha256).hexdigest()


# ── TOTP ────────────────────────────────────────────────────────────────────

def new_totp_secret():
    return pyotp.random_base32()


def provisioning_uri(user, secret):
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=TOTP_ISSUER)


def qr_svg(uri):
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#000000", light="#ffffff")


def confirm_totp(user, secret, code):
    """Enroll ``secret`` if ``code`` is a valid current code for it."""
    counter = _match_totp(secret, code)
    if counter is None:
        return False
    TotpDevice.objects.update_or_create(
        user=user,
        defaults={
            "secret_enc": _fernet().encrypt(secret.encode()).decode(),
            "confirmed_at": timezone.now(),
            "last_counter": counter,
        },
    )
    return True


def _match_totp(secret, code, now=None):
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return None
    totp = pyotp.TOTP(secret)
    base = int((now or time.time()) // 30)
    for offset in (-1, 0, 1):
        counter = base + offset
        if hmac.compare_digest(totp.at(counter * 30), code):
            return counter
    return None


def verify_totp(user, code):
    device = TotpDevice.objects.filter(user=user, confirmed_at__isnull=False).first()
    if device is None:
        return False
    counter = _match_totp(_fernet().decrypt(device.secret_enc.encode()).decode(), code)
    if counter is None:
        return False
    # Atomic compare-and-set: a code (or any older one) can be used only once.
    return TotpDevice.objects.filter(pk=device.pk, last_counter__lt=counter).update(last_counter=counter) == 1


# ── recovery codes ──────────────────────────────────────────────────────────

def generate_recovery_codes(user):
    """Replace the user's codes with a fresh set. Returns the plain codes: show them once."""
    codes = []
    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
        for _ in range(RECOVERY_CODE_COUNT):
            raw = secrets.token_hex(6)  # 48 bits
            codes.append("-".join(raw[i : i + 4] for i in range(0, 12, 4)))
        RecoveryCode.objects.bulk_create(
            [RecoveryCode(user=user, code_hash=_code_hash(c)) for c in codes]
        )
    return codes


def consume_recovery_code(user, code):
    digest = _code_hash(code or "")
    with transaction.atomic():
        row = (
            RecoveryCode.objects.select_for_update()
            .filter(user=user, code_hash=digest, used_at__isnull=True)
            .first()
        )
        if row is None:
            return False
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        return True


def remaining_recovery_codes(user):
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


# ── WebAuthn (FIDO2) ────────────────────────────────────────────────────────

def rp_id(request):
    return settings.WEBAUTHN_RP_ID or request.get_host().split(":")[0]


def origin(request):
    return f"{request.scheme}://{request.get_host()}"


def registration_options(request, user):
    existing = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
        for c in user.webauthn_credentials.all()
    ]
    options = generate_registration_options(
        rp_id=rp_id(request),
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_name=user.email,
        user_id=user.id.bytes,
        user_display_name=user.full_name,
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.CROSS_PLATFORM,
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    request.session[REG_CHALLENGE] = bytes_to_base64url(options.challenge)
    return options_to_json_dict(options)


def register_credential(request, user, payload, name=""):
    challenge = request.session.pop(REG_CHALLENGE, None)
    if not challenge:
        raise ValueError("No registration in progress.")
    verified = verify_registration_response(
        credential=payload,
        expected_challenge=base64url_to_bytes(challenge),
        expected_rp_id=rp_id(request),
        expected_origin=origin(request),
    )
    return WebAuthnCredential.objects.create(
        user=user,
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        name=(name or "Security key")[:80],
    )


def authentication_options(request, user):
    allowed = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id))
        for c in user.webauthn_credentials.all()
    ]
    options = generate_authentication_options(
        rp_id=rp_id(request),
        allow_credentials=allowed,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    request.session[AUTH_CHALLENGE] = bytes_to_base64url(options.challenge)
    return options_to_json_dict(options)


def verify_assertion(request, user, payload):
    """Verify a security-key assertion for ``user``. Returns the credential or None."""
    challenge = request.session.pop(AUTH_CHALLENGE, None)  # single use
    if not challenge:
        return None
    try:
        cred_id = payload["id"] if isinstance(payload, dict) else None
        cred = user.webauthn_credentials.filter(credential_id=cred_id).first()
        if cred is None:
            return None
        verified = verify_authentication_response(
            credential=payload,
            expected_challenge=base64url_to_bytes(challenge),
            expected_rp_id=rp_id(request),
            expected_origin=origin(request),
            credential_public_key=bytes(cred.public_key),
            credential_current_sign_count=cred.sign_count,
        )
    except Exception:  # noqa: BLE001 - any verification failure is simply "not verified"
        return None
    cred.sign_count = verified.new_sign_count
    cred.last_used_at = timezone.now()
    cred.save(update_fields=["sign_count", "last_used_at"])
    return cred


# ── session state ───────────────────────────────────────────────────────────

def hardware_recent(request):
    at = request.session.get(HW_VERIFIED_AT)
    return bool(at and time.time() - at <= settings.HARDWARE_REVERIFY_SECONDS)


def mark_mfa_ok(request, hardware=False):
    request.session[MFA_OK] = True
    if hardware:
        request.session[HW_VERIFIED_AT] = time.time()


def pending_user(request):
    uid = request.session.get(MFA_PENDING)
    at = request.session.get(MFA_PENDING_AT, 0)
    if not uid or time.time() - at > PENDING_TTL_SECONDS:
        return None
    return User.objects.filter(pk=uid, is_active=True).first()
