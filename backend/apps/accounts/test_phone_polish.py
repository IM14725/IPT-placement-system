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
