"""Core security utilities and middleware:
- ``safe_json_dumps``: HTML-safe JSON serialization for inline ``<script>`` blocks.
- ``SecurityHeadersMiddleware``: Content-Security-Policy, Permissions-Policy, Referrer-Policy, nosniff.
- ``MaintenanceModeMiddleware``: Enforces the ``maintenance_mode`` PlatformSetting (503 for non-admins).
"""

import json

from django.conf import settings
from django.http import HttpResponse

MAINTENANCE_EXEMPT_PREFIXES = (
    "/auth/",
    "/platform/",
    "/health",
    "/static/",
    "/i18n/",
    "/api/v1/payments/webhook/",
)


def safe_json_dumps(data) -> str:
    """Serialize *data* to JSON with ``<``, ``>``, and ``&`` escaped as unicode sequences.

    Prevents ``</script>`` breakout or HTML entity injection when interpolated via ``|safe``
    inside a ``<script>`` tag.
    """
    return (
        json.dumps(data)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


class SecurityHeadersMiddleware:
    """Attach baseline defensive HTTP headers to every response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = getattr(
                settings,
                "CONTENT_SECURITY_POLICY",
                (
                    "default-src 'self'; "
                    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "font-src 'self' https://fonts.gstatic.com data:; "
                    "img-src 'self' data: blob:; "
                    "connect-src 'self' ws: wss:; "
                    "frame-ancestors 'none'; "
                    "base-uri 'self'; "
                    "form-action 'self'"
                ),
            )
        return response


class MaintenanceModeMiddleware:
    """Block non-admin traffic with HTTP 503 when ``maintenance_mode`` is enabled."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"
        if path.startswith(MAINTENANCE_EXEMPT_PREFIXES):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and (
            user.is_staff or getattr(user, "is_platform_admin", False)
        ):
            return self.get_response(request)

        if self._is_maintenance_enabled():
            return HttpResponse(
                (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    "<title>Maintenance Mode</title></head>"
                    "<body style='font-family:sans-serif;text-align:center;padding:4rem;'>"
                    "<h1>Under Maintenance</h1>"
                    "<p>IPT Marketplace is currently undergoing scheduled maintenance. "
                    "Please check back shortly.</p></body></html>"
                ),
                status=503,
                content_type="text/html; charset=utf-8",
            )
        return self.get_response(request)

    @staticmethod
    def _is_maintenance_enabled() -> bool:
        try:
            from apps.core.models import PlatformSetting

            row = PlatformSetting.objects.filter(key="maintenance_mode").only("value_bool").first()
            return bool(row and row.value_bool)
        except Exception:  # noqa: BLE001
            return False
