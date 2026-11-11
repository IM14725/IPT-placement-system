"""robots.txt and sitemap.xml for the public pages (welcome, sign-in, registration, legal)."""

from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

PUBLIC_URL_NAMES = ("home", "register", "login", "legal-terms", "legal-privacy")
PRIVATE_PREFIXES = ("/student/", "/organization/", "/platform/", "/api/", "/admin/", "/i18n/", "/report/", "/auth/account/")


@require_GET
@cache_control(max_age=3600, public=True)
def robots_txt(request):
    lines = ["User-agent: *", "Allow: /platform/legal/"]  # legal pages live under /platform/
    lines += [f"Disallow: {prefix}" for prefix in PRIVATE_PREFIXES]
    lines += ["", f"Sitemap: {request.build_absolute_uri(reverse('sitemap'))}", ""]
    return HttpResponse("\n".join(lines), content_type="text/plain")


@require_GET
@cache_control(max_age=3600, public=True)
def sitemap_xml(request):
    urls = "".join(
        f"<url><loc>{request.build_absolute_uri(reverse(name))}</loc></url>" for name in PUBLIC_URL_NAMES
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )
    return HttpResponse(xml, content_type="application/xml")
