import pytest
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, override_settings

from apps.accounts.models import UserRole
from apps.core.audit import _csv_safe, client_ip
from apps.core.models import PlatformSetting
from apps.core.rate_limit import consume
from apps.core.security import safe_json_dumps

User = get_user_model()


def test_m5_safe_json_dumps_escapes_script_breakout_characters():
    raw = {"name": "</script><script>alert(1)</script>&test"}
    encoded = safe_json_dumps(raw)
    assert "</script>" not in encoded
    assert "<" not in encoded and ">" not in encoded and "&" not in encoded
    assert "\\u003c/script\\u003e" in encoded


def test_csv_formula_injection_sanitized():
    assert _csv_safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert _csv_safe("+SUM(A1:A2)") == "'+SUM(A1:A2)"
    assert _csv_safe("-1+2") == "'-1+2"
    assert _csv_safe("@IMPORTXML()") == "'@IMPORTXML()"
    assert _csv_safe("Normal description") == "Normal description"


def test_m1_client_ip_only_trusts_xff_when_configured():
    rf = RequestFactory()
    req = rf.get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1")

    with override_settings(TRUSTED_PROXY_COUNT=0):
        assert client_ip(req) == "10.0.0.1"

    with override_settings(TRUSTED_PROXY_COUNT=1):
        assert client_ip(req) == "10.0.0.1"

    with override_settings(TRUSTED_PROXY_COUNT=2):
        assert client_ip(req) == "203.0.113.5"


def test_m1_rate_limiter_fail_closed_when_redis_unavailable(monkeypatch):
    def _broken_redis():
        raise RuntimeError("Redis connection refused")

    monkeypatch.setattr("apps.core.rate_limit.get_redis", _broken_redis)

    # Default fail-open for non-critical endpoints
    open_res = consume("slot-search", "ip:1.2.3.4", 10, 1.0, fail_closed=False)
    assert open_res.allowed is True

    # Fail-closed for critical auth/payment endpoints
    closed_res = consume("login-ip", "ip:1.2.3.4", 10, 1.0, fail_closed=True)
    assert closed_res.allowed is False
    assert closed_res.retry_after > 0


@pytest.mark.django_db
def test_h5_security_headers_attached_to_responses(client):
    resp = client.get("/platform/")
    assert resp.status_code == 200
    assert resp["X-Content-Type-Options"] == "nosniff"
    assert resp["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in resp["Content-Security-Policy"]
    assert "Permissions-Policy" in resp


@pytest.mark.django_db
def test_m8_maintenance_mode_blocks_public_and_students_but_allows_admins():
    PlatformSetting.objects.update_or_create(
        key="maintenance_mode",
        defaults={"label": "Maintenance Mode", "value_type": "bool", "value_bool": True},
    )
    anon = Client()
    assert anon.get("/").status_code == 503
    # Exempt routes still work during maintenance
    assert anon.get("/platform/").status_code == 200
    assert anon.get("/auth/login/").status_code == 200

    # Student gets 503
    student = User.objects.create_user(
        email="stu@maint.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True
    )
    stu_client = Client()
    stu_client.force_login(student)
    assert stu_client.get("/").status_code == 503

    # Super Admin bypasses maintenance mode
    admin = User.objects.create_superuser(email="admin@maint.tz", password="Secret123!x")
    admin_client = Client()
    admin_client.force_login(admin)
    assert admin_client.get("/").status_code == 200


@pytest.mark.django_db
def test_m6_and_m10_admin_settings_hides_sms_api_key_and_validates_numbers():
    PlatformSetting.objects.update_or_create(
        key="sms_api_key",
        defaults={"label": "Sms Api Key", "value_type": "text", "value_text": "SUPER-SECRET-SMS-KEY-999"},
    )
    admin = User.objects.create_superuser(email="super@settings.tz", password="Secret123!x")
    c = Client()
    c.force_login(admin)

    # 1. GET /platform/settings/ does NOT echo the stored SMS API key in HTML
    get_resp = c.get("/platform/settings/")
    assert get_resp.status_code == 200
    html = get_resp.content.decode("utf-8")
    assert "SUPER-SECRET-SMS-KEY-999" not in html

    assert 'name="sms_api_key"' not in html and 'name="sms_api_endpoint"' not in html  # no gateway fields at all

    # 2. The gateway key/endpoint cannot be changed from the browser, even by a crafted POST
    post_empty = c.post(
        "/platform/settings/",
        {
            "app_fee_amount": "20000",
            "max_applications_per_student": "4",
            "default_slot_limit": "50",
            "sms_api_key": "HIJACKED",
            "sms_api_endpoint": "https://evil.example/sms",
        },
    )
    assert post_empty.status_code == 302
    assert PlatformSetting.objects.get(key="sms_api_key").value_text == "SUPER-SECRET-SMS-KEY-999"
    assert not PlatformSetting.objects.filter(key="sms_api_endpoint", value_text__contains="evil").exists()
    assert PlatformSetting.objects.get(key="app_fee_amount").value_number == 20000

    # 3. Submitting invalid float does not crash with 500; returns 302 with error message and keeps 20000
    post_bad = c.post(
        "/platform/settings/",
        {
            "app_fee_amount": "not-a-number",
            "max_applications_per_student": "4",
            "default_slot_limit": "50",
        },
    )
    assert post_bad.status_code == 302
    assert PlatformSetting.objects.get(key="app_fee_amount").value_number == 20000


@pytest.mark.django_db
def test_deactivated_admin_is_not_platform_admin():
    admin = User.objects.create_user(
        email="deact@admin.tz", password="Secret123!x", role=UserRole.ADMIN, is_active=True
    )
    assert admin.is_platform_admin is True
    admin.is_active = False
    admin.save(update_fields=["is_active"])
    assert admin.is_platform_admin is False
