"""Page-load performance guards: self-hosted assets only (no third-party render-blocking requests),
every icon used by the templates is in the self-hosted icon font, and responses are compressed."""
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.accounts.models import UserRole

BACKEND = Path(__file__).resolve().parents[2]
STATIC = BACKEND / "static"
User = get_user_model()

THIRD_PARTY = ("cdn.tailwindcss.com", "fonts.googleapis.com", "fonts.gstatic.com")


@pytest.mark.django_db
def test_home_page_pulls_nothing_from_third_party_hosts():
    resp = Client().get("/")
    html = resp.content.decode("utf-8")
    for host in THIRD_PARTY:
        assert host not in html, f"{host} must not be referenced (self-host it instead)"
        assert host not in resp["Content-Security-Policy"]
    assert "css/tailwind.css" in html and "css/fonts.css" in html


def test_compiled_assets_exist_and_are_not_empty():
    for rel in (
        "css/tailwind.css",
        "css/fonts.css",
        "fonts/inter-latin.woff2",
        "fonts/material-symbols-outlined.woff2",
        "images/logo-96.webp",
    ):
        path = STATIC / rel
        assert path.exists() and path.stat().st_size > 500, rel
    # The subset icon font is the big win over the 1.1 MB Google font: keep it small.
    assert (STATIC / "fonts/material-symbols-outlined.woff2").stat().st_size < 150_000
    assert (STATIC / "images/logo-96.webp").stat().st_size < 20_000


def test_every_literal_icon_in_templates_is_in_the_icon_subset():
    """Re-run `python scripts/build_fonts.py` if this fails after adding a new icon."""
    available = set((STATIC / "fonts/material-symbols-icons.txt").read_text(encoding="utf-8").split())
    pattern = re.compile(r'class="[^"]*material-symbols-outlined[^"]*"[^>]*>\s*([a-z][a-z0-9_]*)\s*<')
    missing = {}
    for path in (BACKEND / "templates").rglob("*.html"):
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            if name not in available:
                missing.setdefault(name, path.name)
    assert not missing, f"icons missing from the self-hosted font: {missing}"


@pytest.mark.django_db
def test_html_is_gzipped_when_the_browser_accepts_it():
    resp = Client().get("/", HTTP_ACCEPT_ENCODING="gzip")
    assert resp["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in resp["Vary"]


@pytest.mark.django_db
def test_page_has_skeleton_shell_and_stylesheet_failsafe():
    html = Client().get("/").content.decode("utf-8")
    assert 'id="ipt-skel"' in html  # inline first-paint skeleton
    assert "<noscript><link rel=\"stylesheet\"" in html  # no-JS fallback for the async stylesheets
    assert "setTimeout" in html  # the page is always revealed even if a stylesheet never arrives


@pytest.mark.django_db
def test_student_avatar_uses_a_thumbnail_not_the_original(tmp_path, settings):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    from apps.students.models import StudentProfile

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (30, 120, 200)).save(buf, "PNG")

    settings.MEDIA_ROOT = str(tmp_path)
    user = User.objects.create_user(
        email="thumb@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True
    )
    StudentProfile.objects.create(
        user=user, course="CS", education_level=8,
        profile_photo=SimpleUploadedFile("me.png", buf.getvalue(), content_type="image/png"),
    )
    c = Client()
    c.force_login(user)
    html = c.get("/student/profile/").content.decode("utf-8")
    assert "/media/thumbs/" in html
    assert "/media/profile_photos/" not in html  # the full-size original is not loaded in the page


def test_filter_bar_assets_exist_and_are_loaded_on_every_page():
    for rel in ("js/filterbar.js", "js/list-filter.js"):
        assert (STATIC / rel).exists()
    assert ".ipt-filterbar" in (STATIC / "css/responsive.css").read_text(encoding="utf-8")


@pytest.mark.django_db
def test_search_pages_use_the_compact_filter_bar(settings):
    """Welcome page search: keyword/industry box + Filters toggle, with the other fields in a collapsible panel."""
    settings.CACHE_ENABLED = False
    settings.PUBLIC_STATS_MIN = {"organizations": 1, "slots": 1, "universities": 1, "students": 1, "regions": 1}
    from apps.locations.models import District, Region
    from apps.companies.services import create_organization
    from apps.slots.models import Slot
    from apps.students.models import VerificationStatus

    region = Region.objects.create(name="Dar es Salaam", slug="dar")
    district = District.objects.create(name="Kinondoni", region=region)
    owner = User.objects.create_user(email="fb@o.tz", password="Secret123!x", role=UserRole.ORGANIZATION, email_verified=True)
    org = create_organization(owner, "Acme Ltd", email="hr@acme.tz", phone="255700000001")
    org.verification_status = VerificationStatus.APPROVED
    org.save()
    Slot.objects.create(organization=org, title="Dev", industry="Technology", role_type="Internship", district=district, capacity=1)
    html = Client().get("/").content.decode("utf-8")
    assert "data-filterbar" in html and "data-filter-toggle" in html and 'id="search-filter-panel"' in html
    assert html.index('id="search-industry"') < html.index('id="search-filter-panel"') < html.index('id="search-region"')
    assert 'id="search-clear"' in html  # still used by public-search.js
    assert "js/filterbar.js" in html
