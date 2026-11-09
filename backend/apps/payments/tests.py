import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REALTIME_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "realtime")
if REALTIME_DIR not in sys.path:
    sys.path.insert(0, REALTIME_DIR)


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
    # Valid configuration passes cleanly
    validate_production_config(**valid_kwargs)

    # 1. Default or short SECRET_KEY fails
    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        validate_production_config(**{**valid_kwargs, "secret_key": "django-insecure-dev-only-change-me"})

    # 2. Localhost-only or empty ALLOWED_HOSTS fails
    with pytest.raises(ImproperlyConfigured, match="ALLOWED_HOSTS"):
        validate_production_config(**{**valid_kwargs, "allowed_hosts": ["localhost", "127.0.0.1"]})

    # 3. Default GATEWAY_WEBHOOK_SECRET fails
    with pytest.raises(ImproperlyConfigured, match="GATEWAY_WEBHOOK_SECRET"):
        validate_production_config(**{**valid_kwargs, "gateway_webhook_secret": "dev-webhook-secret"})

    # 4. MOCK_API_KEY or MOCK_API_SECRET fails
    with pytest.raises(ImproperlyConfigured, match="SELCOM_API_KEY"):
        validate_production_config(**{**valid_kwargs, "selcom_api_key": "MOCK_API_KEY"})
    with pytest.raises(ImproperlyConfigured, match="SELCOM_API_SECRET"):
        validate_production_config(**{**valid_kwargs, "selcom_api_secret": "MOCK_API_SECRET"})

    # 5. Default DB password fails
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

    assert verify_selcom_webhook_signature(b'{"order_id":"1"}', "any-sig", "2026-01-01T00:00:00Z") is False


