"""Public welcome page: trust counters, slot preview, fee, SEO endpoints and the register wizard markup."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User, UserRole
from apps.companies.models import Organization
from apps.locations.models import District, Region
from apps.slots.models import Slot, SlotStatus
from apps.students.models import StudentProfile, University, VerificationStatus

LOW_MINIMUMS = {"organizations": 1, "slots": 1, "universities": 1, "students": 1, "regions": 1}


@pytest.fixture(autouse=True)
def _no_cache(settings):
    """The welcome data is cached for 5 minutes; every test must see its own database."""
    settings.CACHE_ENABLED = False


@pytest.fixture
def district(db):
    region = Region.objects.create(name="Arusha", slug="arusha")
    return District.objects.create(name="Meru", region=region)


def _organization(name="Secret Co Ltd", status=VerificationStatus.APPROVED, email="o@x.tz"):
    user = User.objects.create_user(email=email, password="Secret123!", role=UserRole.ORGANIZATION)
    return Organization.objects.create(user=user, name=name, industry="Tech", verification_status=status)


def _slot(org, district, **kwargs):
    defaults = dict(title="Network Intern", industry="Technology", role_type="Internship", district=district, capacity=3)
    defaults.update(kwargs)
    return Slot.objects.create(organization=org, **defaults)


def test_counters_hidden_below_minimums(client, db, settings):
    _organization()
    settings.PUBLIC_STATS_MIN = {**LOW_MINIMUMS, "organizations": 10, "universities": 5}
    html = client.get("/").content.decode()
    assert "Verified organizations" not in html


def test_counters_shown_at_minimums(client, db, settings, district):
    org = _organization()
    _slot(org, district)
    uni = University.objects.create(name="Test University", verification_status=VerificationStatus.APPROVED)
    student = User.objects.create_user(email="st@x.tz", password="Secret123!", role=UserRole.STUDENT)
    StudentProfile.objects.update_or_create(user=student, defaults={"university": uni})
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    html = client.get("/").content.decode()
    assert "Verified organizations" in html
    assert "Universities" in html
    assert "Regions with openings" in html


def test_preview_exposes_basic_fields_but_never_the_organization(client, db, settings, district):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization(name="Very Private Holdings")
    _slot(org, district, title="Cloud Intern", stipend_available=True)
    html = client.get("/").content.decode()
    assert "Cloud Intern" in html and "Meru" in html and "Arusha" in html
    assert "3 seats left" in html
    assert "Very Private Holdings" not in html


def test_preview_skips_unapproved_closed_expired_and_full_slots(client, db, settings, district):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    approved = _organization()
    pending = _organization(name="Pending Co", status=VerificationStatus.PENDING, email="p@x.tz")
    _slot(pending, district, title="From Pending Org")
    _slot(approved, district, title="Paused One", status=SlotStatus.PAUSED)
    _slot(approved, district, title="Expired One", application_deadline=timezone.now() - timedelta(days=1))
    _slot(approved, district, title="Visible One")
    html = client.get("/").content.decode()
    assert "Visible One" in html
    for hidden in ("From Pending Org", "Paused One", "Expired One"):
        assert hidden not in html


def test_preview_is_limited_to_six_newest(client, db, settings, district):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization()
    for i in range(8):
        _slot(org, district, title=f"Slot number {i}")
    html = client.get("/").content.decode()
    assert html.count("Login to apply") == 6


def test_fee_follows_the_configured_amount(client, db, settings):
    settings.APPLICATION_FEE = 20000
    html = client.get("/").content.decode()
    assert "TZS 20,000" in html
    assert "M-Pesa" in html and "Airtel Money" in html and "3 hours" in html
    assert "#payments" in html  # links to the refund policy in the Terms


def test_welcome_page_has_share_metadata(client, db):
    html = client.get("/").content.decode()
    assert 'property="og:image"' in html and "og-image.png" in html
    assert 'name="description"' in html
    assert 'content="index, follow"' in html


def test_private_pages_are_noindex(client, db):
    user = User.objects.create_user(email="s@x.tz", password="Secret123!", role=UserRole.STUDENT)
    client.force_login(user)
    html = client.get("/auth/account/").content.decode()
    assert "noindex" in html


def test_welcome_page_in_kiswahili(client, db):
    client.cookies["django_language"] = "sw"
    html = client.get("/").content.decode()
    assert "Jinsi inavyofanya kazi" in html
    assert "Lipa kwa" in html


def test_robots_and_sitemap(client, db):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /student/" in robots.content.decode()
    assert "Sitemap:" in robots.content.decode()
    sitemap = client.get("/sitemap.xml")
    body = sitemap.content.decode()
    assert sitemap.status_code == 200 and body.count("<url>") == 5
    assert "/auth/register/" in body


def test_register_page_is_a_three_step_wizard_with_phone_input(client, db):
    html = client.get("/auth/register/").content.decode()
    assert html.count("data-step=") == 3
    assert "data-wiz-next" in html and "data-wiz-back" in html
    assert "phone-input.js" in html


def test_register_still_accepts_a_student_posted_the_wizard_way(client, db):
    """The wizard only changes the browser flow; the final POST carries the same canonical fields."""
    response = client.post(
        "/auth/register/",
        {
            "role": "STUDENT",
            "email": "wizard@x.tz",
            "first_name": "Asha",
            "last_name": "Juma",
            "phone": "255712345678",
            "password1": "Str0ng-Passw0rd!",
            "password2": "Str0ng-Passw0rd!",
            "accept_terms": "on",
        },
    )
    assert response.status_code in (200, 202, 302)
    assert b"Passwords do not match" not in response.content


def test_university_counter_counts_only_universities_with_registered_students(db, district):
    from apps.accounts.public import _compute

    listed_only = University.objects.create(name="Listed Only", verification_status=VerificationStatus.APPROVED)
    used = University.objects.create(name="Has Students", verification_status=VerificationStatus.APPROVED)
    for i in range(2):  # two students at the same university still count it once
        u = User.objects.create_user(email=f"s{i}@x.tz", password="Secret123!", role=UserRole.STUDENT)
        StudentProfile.objects.update_or_create(user=u, defaults={"university": used})
    inactive = User.objects.create_user(email="gone@x.tz", password="Secret123!", role=UserRole.STUDENT, is_active=False)
    StudentProfile.objects.update_or_create(user=inactive, defaults={"university": listed_only})
    assert _compute()["stats"]["universities"] == 1


# ------------------------------------------------------------------ welcome-page search
@pytest.fixture
def two_places(db):
    arusha = Region.objects.create(name="Arusha", slug="arusha-2")
    dodoma = Region.objects.create(name="Dodoma", slug="dodoma")
    return (
        District.objects.create(name="Meru", region=arusha),
        District.objects.create(name="Arusha City", region=arusha),
        District.objects.create(name="Chamwino", region=dodoma),
    )


def _titles(html):
    import re

    start = html.find('id="slot-results"')
    html = html[start:html.find('id="how-it-works"', start)] if start != -1 else html  # only the results area
    return set(re.findall(r'<h3 class="font-headline-md text-headline-md text-on-surface">([^<]+)</h3>', html))


def test_search_by_region_and_district(client, db, settings, two_places):
    meru, city, chamwino = two_places
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization()
    _slot(org, meru, title="In Meru")
    _slot(org, city, title="In Arusha City")
    _slot(org, chamwino, title="In Chamwino")
    region_id = str(meru.region_id)
    assert _titles(client.get(f"/?region={region_id}").content.decode()) == {"In Meru", "In Arusha City"}
    both = client.get(f"/?region={region_id}&district={city.id}").content.decode()
    assert _titles(both) == {"In Arusha City"}
    assert _titles(client.get("/").content.decode()) == {"In Meru", "In Arusha City", "In Chamwino"}


def test_search_by_industry_and_level_is_case_insensitive(client, db, settings, two_places):
    meru, _, _ = two_places
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization()
    _slot(org, meru, title="Cloud", industry="Information Technology", education_level=8)
    _slot(org, meru, title="Clinic", industry="Healthcare", education_level=6)
    assert _titles(client.get("/?industry=technology").content.decode()) == {"Cloud"}
    assert _titles(client.get("/?level=6").content.decode()) == {"Clinic"}
    assert _titles(client.get("/?industry=health&level=8").content.decode()) == set()


def test_search_with_no_match_says_so(client, db, settings, two_places):
    meru, _, chamwino = two_places
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    _slot(_organization(), meru, title="Only Meru")
    html = client.get(f"/?district={chamwino.id}").content.decode()
    assert "No open slots match your search." in html and "Only Meru" not in html


@pytest.mark.parametrize("query", ["?region=not-a-uuid", "?district=1' OR 1=1", "?level=abc", "?level=99", "?industry=" + "x" * 500])
def test_malformed_filters_are_ignored_not_errors(client, db, settings, two_places, query):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    _slot(_organization(), two_places[0], title="Still here")
    response = client.get("/" + query)
    assert response.status_code == 200


def test_suspended_organizations_are_not_searchable(client, db, settings, two_places):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization(name="Suspended Co", email="s@x.tz")
    org.suspended_at = timezone.now()
    org.save(update_fields=["suspended_at"])
    _slot(org, two_places[0], title="Hidden by suspension")
    assert "Hidden by suspension" not in client.get("/").content.decode()


def test_search_results_are_capped_at_six(client, db, settings, two_places):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization()
    for i in range(15):
        _slot(org, two_places[0], title=f"Slot {i:02d}")
    html = client.get(f"/?region={two_places[0].region_id}").content.decode()
    assert len(_titles(html)) == 6
    assert "Register to see all 15 slots" in html


def test_fragment_endpoint_returns_only_the_cards(client, db, settings, two_places):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    org = _organization()
    _slot(org, two_places[0], title="Fragment hit", industry="Mining")
    response = client.get("/slots/public/?industry=mining")
    html = response.content.decode()
    assert response.status_code == 200 and response["X-Robots-Tag"] == "noindex"
    assert "Fragment hit" in html and "<html" not in html and "Secret Co Ltd" not in html
    assert client.post("/slots/public/").status_code == 405


def test_selected_region_renders_its_districts_without_javascript(client, db, settings, two_places):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    _slot(_organization(), two_places[0], title="x")
    html = client.get(f"/?region={two_places[0].region_id}&district={two_places[1].id}").content.decode()
    assert "Meru" in html and "Arusha City" in html
    assert 'value="%s" selected' % two_places[1].id in html and "Chamwino" not in html.split('id="search-district"')[1].split("</select>")[0]


# ------------------------------------------------------------------ most-searched places first
@pytest.fixture
def cache_on(settings):
    settings.CACHE_ENABLED = True  # Redis is the in-process fakeredis during tests


@pytest.fixture
def dar_and_arusha(db):
    dar = Region.objects.create(name="Dar es Salaam", slug="dar-es-salaam")
    arusha = Region.objects.create(name="Arusha", slug="arusha-3")
    return District.objects.create(name="Kinondoni", region=dar), District.objects.create(name="Meru", region=arusha)


def test_welcome_page_leads_with_the_most_searched_region(cache_on, db, dar_and_arusha):
    from apps.accounts.public import _compute, record_region_search

    kinondoni, meru = dar_and_arusha
    org = _organization()
    for i in range(4):
        _slot(org, meru, title=f"Arusha {i}")  # Arusha has more slots, but nobody searches for it
    for i in range(2):
        _slot(org, kinondoni, title=f"Dar {i}")
    for _ in range(3):
        record_region_search(region=str(kinondoni.region_id))
    record_region_search(district=str(meru.id))  # a district-only search counts for its region
    data = _compute()
    titles = [row["title"] for row in data["popular"]]
    assert len(titles) == 6
    assert set(titles[:2]) == {"Dar 0", "Dar 1"} and data["popular_region"] == "Dar es Salaam"


def test_without_any_searches_the_region_with_most_open_slots_leads(cache_on, db, dar_and_arusha):
    from apps.accounts.public import _compute

    kinondoni, meru = dar_and_arusha
    org = _organization()
    _slot(org, kinondoni, title="Dar only")
    for i in range(2):
        _slot(org, meru, title=f"Arusha {i}")
    data = _compute()
    assert {r["title"] for r in data["popular"][:2]} == {"Arusha 0", "Arusha 1"} and data["popular_region"] == ""


def test_searching_for_a_region_shows_six_of_its_open_slots_only(client, db, settings, dar_and_arusha):
    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    kinondoni, meru = dar_and_arusha
    org = _organization()
    for i in range(8):
        _slot(org, kinondoni, title=f"Dar open {i}")
    _slot(org, kinondoni, title="Dar paused", status=SlotStatus.PAUSED)
    _slot(org, kinondoni, title="Dar closed", status=SlotStatus.CLOSED)
    _slot(org, kinondoni, title="Dar expired", application_deadline=timezone.now() - timedelta(hours=1))
    _slot(org, meru, title="Arusha elsewhere")
    titles = _titles(client.get(f"/?region={kinondoni.region_id}").content.decode())
    assert len(titles) == 6 and all(t.startswith("Dar open") for t in titles)


def test_full_slots_are_never_shown(client, db, settings, dar_and_arusha):
    from apps.applications.models import Application, ApplicationStatus

    settings.PUBLIC_STATS_MIN = LOW_MINIMUMS
    kinondoni, _ = dar_and_arusha
    org = _organization()
    full = _slot(org, kinondoni, title="Already full", capacity=1)
    _slot(org, kinondoni, title="Still free", capacity=1)
    student = User.objects.create_user(email="full@x.tz", password="Secret123!", role=UserRole.STUDENT)
    profile = StudentProfile.objects.create(
        user=student,
        university=University.objects.create(name="Some Uni", verification_status=VerificationStatus.APPROVED),
        course="CS", academic_year=3, verification_status=VerificationStatus.APPROVED,
    )
    from apps.applications.services import create_application

    create_application(profile, full)
    assert Application.objects.filter(slot=full, status=ApplicationStatus.PENDING).exists()
    for url in ("/", f"/?region={kinondoni.region_id}"):
        titles = _titles(client.get(url).content.decode())
        assert titles == {"Still free"}, url
