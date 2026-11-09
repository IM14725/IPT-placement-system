import base64
import hashlib
import hmac
import json
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REALTIME_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "realtime")
if REALTIME_DIR not in sys.path:
    sys.path.insert(0, REALTIME_DIR)


def _sign_selcom(body_bytes: bytes, timestamp: str, secret: str) -> str:
    msg = f"timestamp={timestamp}&body={body_bytes.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg=msg, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def test_c1_mock_endpoints_disabled_by_default(monkeypatch):
    from app.core.config import settings as rt_settings
    import app.webhooks.router as rt_router
    from main import app

    monkeypatch.setattr(rt_settings, "enable_mock_gateway", False)
    monkeypatch.setattr(rt_router, "enforce_rate_limit", lambda *a, **kw: None)

    client = TestClient(app)
    r1 = client.post(
        "/api/v1/payments/mock/initiate",
        json={"reference_id": "IPT-123", "amount": "15000.00"},
    )
    assert r1.status_code == 404

    r2 = client.post(
        "/api/v1/payments/mock/callback",
        json={"reference_id": "IPT-123", "status": "PAID", "gateway_txn_id": "TX1", "amount": "15000.00"},
    )
    assert r2.status_code == 404


def test_c1_mock_signature_cannot_forge_real_callback_and_mock_requires_signature(monkeypatch):
    from app.core.config import settings as rt_settings
    from app.core.security import canonical, sign_mock, verify, verify_mock
    import app.webhooks.router as rt_router
    from main import app

    monkeypatch.setattr(rt_settings, "enable_mock_gateway", True)

    async def _noop_rl(*args, **kwargs):
        return None

    monkeypatch.setattr(rt_router, "enforce_rate_limit", _noop_rl)

    payload_str = canonical("IPT-ABC", "PAID", "MOCK-IPT-ABC", "15000.00")
    mock_sig = sign_mock(payload_str)

    # Mock signature is valid for mock verifier, but MUST be rejected by real callback verifier
    assert verify_mock(payload_str, mock_sig) is True
    assert verify(payload_str, mock_sig) is False

    client = TestClient(app)
    # 1. Mock callback without signature -> 401
    r_unsigned = client.post(
        "/api/v1/payments/mock/callback",
        json={"reference_id": "IPT-ABC", "status": "PAID", "gateway_txn_id": "MOCK-IPT-ABC", "amount": "15000.00"},
    )
    assert r_unsigned.status_code == 401

    # 2. Real callback with a mock signature -> 401
    r_forged = client.post(
        "/api/v1/payments/callback",
        headers={"X-Gateway-Signature": mock_sig},
        json={"reference_id": "IPT-ABC", "status": "PAID", "gateway_txn_id": "MOCK-IPT-ABC", "amount": "15000.00"},
    )
    assert r_forged.status_code == 401


def test_c1_amount_equality_helper():
    from app.webhooks.router import _amounts_equal

    assert _amounts_equal("15000", "15000.00") is True
    assert _amounts_equal("15000.00", "10000.00") is False
    assert _amounts_equal("", "15000.00") is False
    assert _amounts_equal("invalid", "15000.00") is False


def test_c4_production_config_rejects_missing_or_default_secrets():
    from django.core.exceptions import ImproperlyConfigured
    from config.settings.base import validate_production_config

    valid_kwargs = dict(
        secret_key="a" * 50,
        allowed_hosts=["ipt.ac.tz"],
        gateway_webhook_secret="prod-webhook-secret-999999",
        selcom_api_key="LIVE_SELCOM_KEY",
        selcom_api_secret="LIVE_SELCOM_SECRET_VALUE",
        selcom_vendor_till="12345678",
        database_url="postgres://ipt:strong_prod_pass@db:5432/ipt",
        db_password="",
    )
    validate_production_config(**valid_kwargs)

    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        validate_production_config(**{**valid_kwargs, "secret_key": "django-insecure-dev-only-change-me"})

    with pytest.raises(ImproperlyConfigured, match="ALLOWED_HOSTS"):
        validate_production_config(**{**valid_kwargs, "allowed_hosts": ["localhost", "127.0.0.1"]})

    with pytest.raises(ImproperlyConfigured, match="GATEWAY_WEBHOOK_SECRET"):
        validate_production_config(**{**valid_kwargs, "gateway_webhook_secret": "dev-webhook-secret"})

    with pytest.raises(ImproperlyConfigured, match="SELCOM_API_KEY"):
        validate_production_config(**{**valid_kwargs, "selcom_api_key": "MOCK_API_KEY"})
    with pytest.raises(ImproperlyConfigured, match="SELCOM_API_SECRET"):
        validate_production_config(**{**valid_kwargs, "selcom_api_secret": "MOCK_API_SECRET"})

    with pytest.raises(ImproperlyConfigured, match="DATABASE_URL"):
        validate_production_config(**{**valid_kwargs, "database_url": "postgres://ipt:ipt_dev_password@db:5432/ipt"})


def test_c4_realtime_settings_reject_insecure_defaults_in_prod():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="GATEWAY_WEBHOOK_SECRET"):
        Settings(django_env="prod", gateway_webhook_secret="dev-webhook-secret")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(
            django_env="prod",
            gateway_webhook_secret="prod-webhook-secret-999999",
            database_url="postgresql+asyncpg://ipt:ipt_dev_password@localhost:5432/ipt_marketplace",
        )


def test_c4_selcom_mock_secrets_rejected_when_debug_is_false(settings):
    from apps.payments.utils import send_selcom_wallet_pull, verify_selcom_webhook_signature

    settings.DEBUG = False
    settings.SELCOM_API_KEY = "MOCK_API_KEY"
    settings.SELCOM_API_SECRET = "MOCK_API_SECRET"

    code, data = send_selcom_wallet_pull("IPT-TEST", "255712345678", 15000)
    assert code == 500
    assert data["result"] == "FAIL"

    now_iso = datetime.now(timezone.utc).isoformat()
    assert verify_selcom_webhook_signature(b'{"order_id":"1"}', "any-sig", now_iso) is False


def test_h3_selcom_webhook_rejects_expired_timestamps_and_verifies_fresh_ones(settings):
    from apps.payments.utils import verify_selcom_webhook_signature

    settings.DEBUG = False
    settings.SELCOM_API_SECRET = "live-selcom-secret-key-12345"
    body = b'{"order_id":"IPT-100","resultcode":"000","amount":15000}'

    # Fresh timestamp (now) -> True
    fresh_ts = str(int(time.time()))
    sig_fresh = _sign_selcom(body, fresh_ts, settings.SELCOM_API_SECRET)
    assert verify_selcom_webhook_signature(body, sig_fresh, fresh_ts) is True

    # Stale timestamp (1 hour ago) with valid HMAC -> False (replay blocked)
    stale_ts = str(int(time.time()) - 3600)
    sig_stale = _sign_selcom(body, stale_ts, settings.SELCOM_API_SECRET)
    assert verify_selcom_webhook_signature(body, sig_stale, stale_ts) is False


@pytest.mark.django_db
def test_h3_selcom_webhook_enforces_amount_currency_and_unique_transid(client, settings):
    from apps.accounts.models import User, UserRole
    from apps.applications.models import Application
    from apps.companies.models import Organization
    from apps.locations.models import District, Region
    from apps.payments.models import Payment, PaymentStatus
    from apps.payments.utils import get_application_fee
    from apps.slots.models import Slot
    from apps.students.models import StudentProfile

    settings.DEBUG = False
    settings.SELCOM_API_SECRET = "live-selcom-secret-key-12345"

    assert get_application_fee() == Decimal("15000.00")

    reg = Region.objects.create(name="Dar es Salaam", slug="dar-es-salaam")
    dist = District.objects.create(region=reg, name="Ilala")
    org_user = User.objects.create_user(email="org@h3.tz", password="Secret123!x", role=UserRole.ORGANIZATION)
    org = Organization.objects.create(user=org_user, name="H3 Corp", industry="IT", region=reg, district=dist)
    slot = Slot.objects.create(organization=org, title="Dev Intern", department="IT", district=dist, capacity=5)

    stu_user = User.objects.create_user(email="stu@h3.tz", password="Secret123!x", role=UserRole.STUDENT)
    stu = StudentProfile.objects.create(user=stu_user, student_id="S1", course="CS", region=reg, district=dist)
    app_obj = Application.objects.create(student=stu, slot=slot)
    payment = Payment.objects.create(student=stu, application=app_obj, amount=Decimal("15000.00"), currency="TZS")

    ts = datetime.now(timezone.utc).isoformat()

    # 1. Mismatched amount (100 instead of 15000) -> 400, remains PENDING
    bad_amt_body = json.dumps({
        "order_id": payment.reference_id,
        "resultcode": "000",
        "amount": 100,
        "currency": "TZS",
        "transid": "TXN-100",
    }).encode("utf-8")
    sig1 = _sign_selcom(bad_amt_body, ts, settings.SELCOM_API_SECRET)
    r1 = client.post(
        "/api/payments/webhook/",
        data=bad_amt_body,
        content_type="application/json",
        HTTP_X_SELCOM_SIGNATURE=sig1,
        HTTP_X_SELCOM_TIMESTAMP=ts,
    )
    assert r1.status_code == 400
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING

    # 2. Exact amount & valid signature -> 200, marks PAID
    good_body = json.dumps({
        "order_id": payment.reference_id,
        "resultcode": "000",
        "amount": 15000,
        "currency": "TZS",
        "transid": "TXN-GOOD-1",
    }).encode("utf-8")
    sig2 = _sign_selcom(good_body, ts, settings.SELCOM_API_SECRET)
    r2 = client.post(
        "/api/payments/webhook/",
        data=good_body,
        content_type="application/json",
        HTTP_X_SELCOM_SIGNATURE=sig2,
        HTTP_X_SELCOM_TIMESTAMP=ts,
    )
    assert r2.status_code == 200
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID

    # 3. Replaying the same transid on a second order -> 400 Duplicate transaction ID
    slot2 = Slot.objects.create(organization=org, title="QA Intern", department="QA", district=dist, capacity=5)
    app2 = Application.objects.create(student=stu, slot=slot2)
    payment2 = Payment.objects.create(student=stu, application=app2, amount=Decimal("15000.00"), currency="TZS")
    dup_body = json.dumps({
        "order_id": payment2.reference_id,
        "resultcode": "000",
        "amount": 15000,
        "currency": "TZS",
        "transid": "TXN-GOOD-1",
    }).encode("utf-8")
    sig3 = _sign_selcom(dup_body, ts, settings.SELCOM_API_SECRET)
    r3 = client.post(
        "/api/payments/webhook/",
        data=dup_body,
        content_type="application/json",
        HTTP_X_SELCOM_SIGNATURE=sig3,
        HTTP_X_SELCOM_TIMESTAMP=ts,
    )
    assert r3.status_code == 400
    payment2.refresh_from_db()
    assert payment2.status == PaymentStatus.PENDING
