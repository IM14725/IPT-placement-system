"""Phone-screen polish fixes from the mobile review (WhatsApp button, account card, labels, tap targets, ...)."""

import pytest

FORM_PAGES = ["/auth/login/", "/auth/register/", "/auth/forgot-password/"]


@pytest.mark.django_db
def test_whatsapp_button_is_bottom_right_on_phones_and_absent_on_form_pages(client):
    home = client.get("/").content.decode()
    float_link = next(l for l in home.split("<a ") if "wa.me/255756322833?text=" in l).split(">")[0]  # opening tag only
    assert "right-3" in float_link and "w-11" in float_link and "sm:left-md" in float_link
    assert "left-3" not in float_link  # no longer pinned to the left edge where it covered content
    for url in FORM_PAGES:
        body = client.get(url).content.decode()
        assert "wa.me/255756322833?text=" not in body, url  # the floating button, not the footer link
        assert "WhatsApp: +255 756 322 833" in body, url  # the footer link stays


# ── 2. the old "Account" card is gone; the header carries what it showed ──────────

from django.urls import reverse  # noqa: E402

from apps.students.test_dashboard_features import _client, _reset_redis_state, _student, org, places  # noqa: E402,F401


@pytest.mark.django_db
def test_profile_pages_have_no_second_account_card(places, org):
    student = _student(places)
    student.student_id = "2022-04-01234"
    student.gender = "F"
    student.save()
    body = _client(student.user).get(reverse("student-profile")).content.decode()
    assert 'data-account-header' in body
    assert "verified_user" not in body and ">Active<" not in body  # the old card's icon and badge
    header = body[body.index("data-account-header") : body.index("</section>", body.index("data-account-header"))]
    assert "2022-04-01234" in header and "Student ID" in header  # moved into the header

    body = _client(org.user).get(reverse("organization-profile")).content.decode()
    assert "verified_user" not in body
    assert "Organization Documents" in body  # the rest of that column stays


# ── 3. section headings line up with the fields below them ───────────────────────

@pytest.mark.django_db
def test_form_card_headings_put_the_icon_inline_so_text_aligns_with_labels(places, org):
    import re

    for client, url, title in (
        (_client(_student(places).user), reverse("student-profile"), "Academic Information"),
        (_client(org.user), reverse("organization-profile"), "Organization Information"),
    ):
        body = client.get(url).content.decode()
        heading = re.search(r"<h2[^>]*>(.*?)</h2>", body[body.index(title) - 400 : body.index(title) + 100], re.S)
        assert heading and "material-symbols-outlined" in heading.group(1) and title in heading.group(1), url


# ── 4. dashboard stat labels are single words (no mid-word break on a 360 px screen) ──

@pytest.mark.django_db
def test_student_dashboard_stat_labels_are_single_words(places):
    body = _client(_student(places).user).get(reverse("student-dashboard")).content.decode()
    assert "Applications sent" not in body
    for label in ("Applied", "Paid", "Accepted"):
        assert f">{label}</span>" in body


# ── 5. Find a Slot: Region / District filters use short labels that fit side by side ──

@pytest.mark.django_db
def test_marketplace_region_district_filters_use_short_labels(places):
    body = _client(_student(places).user).get(reverse("student-marketplace")).content.decode()
    assert '<option value="">Region</option>' in body and '<option value="">District</option>' in body
    assert "All Regions" not in body and "All Districts" not in body


# ── 6. tap targets are at least 40 px on phones ────────────────────────────────────

def test_phone_css_sets_a_40px_tap_target_for_links_and_buttons():
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "static" / "css" / "responsive.css").read_text(encoding="utf-8")
    block = css[css.index("every tap target is at least 40 px high") :]
    assert "@media (max-width: 767px)" in block
    for selector in ("footer a", "#topbar a", "main button", "main summary", "main select", ".ipt-file-input"):
        assert selector in block, selector
    assert block.count("min-height: 40px") >= 2
    assert "content: \"\"" in block  # the invisible tap area for inline links


# ── 7. no text under 12 px ────────────────────────────────────────────────────────

def test_no_text_smaller_than_12px_in_templates_or_theme():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    config = (root / "tailwind.config.js").read_text(encoding="utf-8")
    assert '"label-caps": ["12px"' in config  # the size behind badges and small caps labels
    small = re.compile(r"text-\[(?:[0-9]|1[01])px\]")
    offenders = [str(p.relative_to(root)) for p in (root / "templates").rglob("*.html") if small.search(p.read_text(encoding="utf-8"))]
    assert not offenders, offenders
    inline = re.compile(r"font-size:\s*(?:[0-9]|1[01])px")
    for name in ("templates/base.html",):
        assert not inline.search((root / name).read_text(encoding="utf-8")), name


# ── 8. profile pages: collapsible sections, GPA label, location placeholders ───────

@pytest.mark.django_db
def test_student_profile_is_split_into_sections_and_loses_no_field(places):
    import re

    from apps.students.forms import StudentProfileForm

    student = _student(places)
    body = _client(student.user).get(reverse("student-profile")).content.decode()
    sections = re.findall(r'<details class="ipt-section"( open)?>', body)
    assert len(sections) == 5
    assert sections.count(" open") == 3  # personal, academic and location start open; photos and skills closed
    for title in ("Personal details", "Academic", "Location", "Photos", "Skills"):
        assert title in body, title
    # Every form field is still rendered exactly where the user can reach it.
    for name in StudentProfileForm(user=student.user).fields:
        assert f'name="{name}"' in body, name
    assert "GPA" in body
    assert "Gpa" not in body
    assert "form-sections.js" in body


@pytest.mark.django_db
def test_organization_profile_is_split_into_sections_and_loses_no_field(org):
    from apps.companies.forms import OrganizationProfileForm

    body = _client(org.user).get(reverse("organization-profile")).content.decode()
    assert body.count('<details class="ipt-section"') == 3  # details, location, documents
    for name in OrganizationProfileForm().fields:
        assert f'name="{name}"' in body, name
    assert "Organization details" in body and "Organization Documents" in body


def test_location_dropdowns_show_their_placeholder_while_disabled():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "static" / "js" / "location-cascade.js").read_text(encoding="utf-8")
    # The placeholder option is always selected; before this a disabled list rendered blank.
    assert 'buildOption("", placeholder, true, disabled)' in js
    assert "!disabled, disabled" not in js


# ── 9. login / register: one H1 and no empty band above the form on phones ─────────

@pytest.mark.django_db
def test_login_and_register_have_a_single_h1_and_no_forced_card_height_on_phones(client):
    for url in ("/auth/login/", "/auth/register/", "/auth/register/?role=student", "/auth/forgot-password/"):
        body = client.get(url).content.decode()
        assert body.count("<h1") == 1, url
        assert "lg:min-h-[640px]" in body and " min-h-[640px]" not in body.replace("lg:min-h-[640px]", ""), url
        assert "items-start lg:items-center" in body, url  # form sits at the top of the card on phones
