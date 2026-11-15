"""Find a Slot (recommendations on top, quick filters, time left), notifications + alerts on one page,
and profile + security on one page."""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.core.timeleft import time_left_text
from apps.locations.models import Ward
from apps.slots.models import Slot
from apps.slots.views import _signature
from apps.students.forms import StudentProfileForm
from apps.students.models import SavedSearch, SavedSlot

# Fixtures and helpers shared with the other student dashboard tests.
from apps.students.test_dashboard_features import (  # noqa: F401
    _client,
    _reset_redis_state,
    _slot,
    _student,
    org,
    places,
)


# ── time left: hours, or minutes under an hour ──────────────────────────────

def test_time_left_is_hours_then_minutes():
    now = timezone.now()
    assert time_left_text(None) == "No deadline"
    assert time_left_text(now - timedelta(seconds=1), now) == "Applications closed"
    # Two or more days read in days; under that, hours; under one hour, minutes.
    assert time_left_text(now + timedelta(days=30, minutes=1), now) == "30 days left"
    assert time_left_text(now + timedelta(days=2, minutes=1), now) == "2 days left"
    assert time_left_text(now + timedelta(hours=47, minutes=59), now) == "47 hours left"
    assert time_left_text(now + timedelta(hours=36, minutes=59), now) == "36 hours left"
    assert time_left_text(now + timedelta(hours=1, seconds=5), now) == "1 hour left"
    # Under one hour the unit becomes minutes, and never reads "60 minutes".
    assert time_left_text(now + timedelta(minutes=59, seconds=30), now) == "59 minutes left"
    assert time_left_text(now + timedelta(minutes=25, seconds=10), now) == "25 minutes left"
    assert time_left_text(now + timedelta(seconds=20), now) == "1 minute left"


@pytest.mark.django_db
def test_slot_cards_show_time_left_under_the_slot(places, org):
    student = _student(places)
    now = timezone.now()
    for title, deadline in (
        ("Hours", now + timedelta(hours=5, minutes=30)),
        ("Minutes", now + timedelta(minutes=25, seconds=30)),
        ("Days", now + timedelta(days=12, hours=3)),
    ):
        SavedSlot.objects.create(student=student, slot=_slot(org, places["kinondoni"], title, application_deadline=deadline))
    body = _client(student.user).get(reverse("student-saved-slots")).content.decode()
    assert "data-timeleft" in body
    assert "5 hours left" in body or "4 hours left" in body
    assert "25 minutes left" in body or "24 minutes left" in body
    assert "12 days left" in body or "11 days left" in body


# ── Find a Slot: recommendations on top, quick filters, "New" flag ───────────

@pytest.mark.django_db
def test_find_a_slot_has_recommendations_on_top_and_quick_filter_chips(places, org):
    student = _student(places)
    _slot(org, places["kinondoni"], "Best match", education_level=8, academic_year=3)
    _slot(org, places["meru"], "Far away", department="Accounting", industry="Finance")
    page = _client(student.user).get(reverse("student-marketplace"))
    assert page.status_code == 200
    rows = page.context["recommended"]
    assert [row["title"] for row in rows] == ["Best match"]
    assert rows[0]["match"] == 100 and "In your district" in rows[0]["reasons"]
    assert rows[0]["available_count"] == 2 and "is_new" in rows[0]
    body = page.content.decode()
    assert 'id="recommended-block"' in body and 'id="recommended-data"' in body
    assert 'data-quick="closing"' in body and 'data-quick="new"' in body
    assert 'href="/notifications/#alerts"' in body  # "My alerts" now leads to the notifications page


@pytest.mark.django_db
def test_find_a_slot_without_a_profile_has_no_recommendation_block(places, org):
    from apps.accounts.models import UserRole
    from django.contrib.auth import get_user_model

    _slot(org, places["kinondoni"])
    user = get_user_model().objects.create_user(
        email="bare@x.tz", password="Secret123!x", role=UserRole.STUDENT, email_verified=True
    )
    page = _client(user).get(reverse("student-marketplace"))
    assert page.status_code == 200 and page.context["recommended"] == []
    assert 'id="recommended-block"' not in page.content.decode()


@pytest.mark.django_db
def test_search_api_quick_filters_closing_and_new(places, org):
    student = _student(places)
    now = timezone.now()
    _slot(org, places["kinondoni"], "Closing", application_deadline=now + timedelta(days=2))
    _slot(org, places["kinondoni"], "Later", application_deadline=now + timedelta(days=30))
    old = _slot(org, places["kinondoni"], "Old")
    Slot.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=10))
    c = _client(student.user)

    def titles(**params):
        return [row["title"] for row in c.get("/api/slots/search/", params).json()["results"]]

    assert titles(quick="closing") == ["Closing"]
    assert set(titles(quick="new")) == {"Closing", "Later"}
    assert set(titles()) == {"Closing", "Later", "Old"}
    assert set(titles(quick="bogus")) == {"Closing", "Later", "Old"}  # unknown value is ignored
    flags = {row["title"]: row["is_new"] for row in c.get("/api/slots/search/").json()["results"]}
    assert flags == {"Closing": True, "Later": True, "Old": False}
    # The cached listing is keyed by the filter, so one chip can never serve another's results.
    assert _signature("", "", "", "", "", "", "closing") != _signature("", "", "", "", "", "", "")
    assert _signature("", "", "", "", "", "", "closing") != _signature("", "", "", "", "", "", "new")


@pytest.mark.django_db
def test_dashboard_has_no_recommended_or_closing_soon_previews(places, org):
    """Those live in Find a Slot only; the dashboard keeps its next step, stats, saved slots and applications."""
    student = _student(places)
    _slot(org, places["kinondoni"], "Closing", application_deadline=timezone.now() + timedelta(days=2))
    page = _client(student.user).get(reverse("student-dashboard"))
    body = page.content.decode()
    assert page.status_code == 200
    assert "Recommended for you" not in body and "?quick=closing" not in body
    assert reverse("student-recommended") not in body and reverse("student-discover") not in body
    assert "recommended" not in page.context and "closing_soon" not in page.context
    assert "Profile completeness" in body and "Recent Applications" in body


# ── notifications and alerts: one page ──────────────────────────────────────

@pytest.mark.django_db
def test_alerts_live_on_the_notifications_page(places, org):
    student = _student(places)
    alert = SavedSearch.objects.create(student=student, region=places["dar"], department="Computer")
    c = _client(student.user)
    back = reverse("notifications") + "#alerts"

    assert c.get(reverse("student-alerts")).url == back
    page = c.get(reverse("notifications"))
    body = page.content.decode()
    assert page.status_code == 200 and page.context["show_alerts"] and len(page.context["alerts"]) == 1
    assert 'data-panel="notifications"' in body and 'data-panel="alerts"' in body and "Computer" in body
    assert 'data-sub-match="#alerts"' in body and 'role="tablist"' not in body

    resp = c.post(reverse("student-alerts"), {"action": "toggle", "alert_id": str(alert.id)})
    assert resp.status_code == 302 and resp.url == back
    alert.refresh_from_db()
    assert alert.is_active is False
    resp = c.post(reverse("student-alerts"), {"action": "delete", "alert_id": str(alert.id)})
    assert resp.url == back and not SavedSearch.objects.exists()

    # Organizations have notifications only: no alerts tab.
    org_page = _client(org.user).get(reverse("notifications"))
    assert not org_page.context["show_alerts"] and 'data-tab="alerts"' not in org_page.content.decode()


@pytest.mark.django_db
def test_side_menu_has_no_separate_pages_for_what_moved(places, org):
    student_menu = _client(_student(places).user).get(reverse("student-dashboard")).content.decode()
    for gone in (reverse("student-recommended"), reverse("student-discover"), reverse("student-alerts"), "/auth/account/"):
        assert f'href="{gone}"' not in student_menu, gone
    assert reverse("student-marketplace") in student_menu and "Notifications &amp; alerts" in student_menu
    org_menu = _client(org.user).get(reverse("organization-dashboard")).content.decode()
    assert 'href="/auth/account/"' not in org_menu


# ── profile and security: one page ──────────────────────────────────────────

@pytest.mark.django_db
def test_student_profile_and_security_share_one_page(places):
    student = _student(places)
    c = _client(student.user)
    assert c.get("/auth/account/").url == reverse("student-profile") + "#security"

    page = c.get(reverse("student-profile"))
    body = page.content.decode()
    assert page.status_code == 200
    # Sections are chosen from the side drawer now (links ending in #profile / #security), not an in-page tab bar.
    assert 'data-panel="profile"' in body and 'data-panel="security"' in body
    assert 'data-sub-match="#profile"' in body and 'data-sub-match="#security"' in body
    assert 'role="tablist"' not in body
    for action in ("/auth/account/email/", "/auth/account/phone/", "/auth/change-password/"):
        assert f'action="{action}"' in body
    # The profile form and the security forms must not clash: every security field id has its own prefix.
    assert body.count('id="id_phone"') == 1
    assert 'id="sec_phone_phone"' in body and 'id="sec_email_current_password"' in body and 'id="sec_pwd_password1"' in body
    assert body.count('id="sec_phone_current_password"') == 1


@pytest.mark.django_db
def test_organization_profile_and_security_share_one_page(org):
    c = _client(org.user)
    assert c.get("/auth/account/").url == reverse("organization-profile") + "#security"
    body = c.get(reverse("organization-profile")).content.decode()
    # Sections are chosen from the side drawer now (links ending in #profile / #security), not an in-page tab bar.
    assert 'data-panel="profile"' in body and 'data-panel="security"' in body
    assert 'data-sub-match="#profile"' in body and 'data-sub-match="#security"' in body
    assert 'role="tablist"' not in body
    assert 'action="/auth/account/email/"' in body and 'action="/auth/change-password/"' in body
    # Three tabs, each panel holding only its own part.
    assert 'data-panel="team"' in body and 'data-sub-match="#team"' in body
    panels = {n: body[body.index(f'data-panel="{n}"'):] for n in ("profile", "team", "security")}
    profile = panels["profile"][: panels["profile"].index('data-panel="team"')]
    team = panels["team"][: panels["team"].index('data-panel="security"')]
    assert "Organization Information" in profile and "Organization Team Accounts" not in profile
    assert "Organization Team Accounts" in team and "Organization Information" not in team
    assert "/auth/change-password/" not in profile and "/auth/change-password/" not in team


def _profile_post(places, phone):
    ward, _ = Ward.objects.get_or_create(name="Mwananyamala", district=places["kinondoni"])
    return {
        "phone": phone,
        "course": "Computer Science",
        "region": str(places["dar"].id),
        "district": str(places["kinondoni"].id),
        "ward": str(ward.id),
    }


@pytest.mark.django_db
def test_changing_the_phone_in_the_profile_needs_a_code_sent_to_the_new_number(places, capsys):
    student = _student(places)
    user, old = student.user, student.user.phone
    c = _client(user)

    resp = c.post(reverse("student-profile"), _profile_post(places, "255700000099"))
    assert resp.status_code == 302 and resp.url == reverse("verify-contact", kwargs={"kind": "phone"})
    out = capsys.readouterr().out
    assert "[SMS:MOCK] -> 255700000099" in out  # the code goes to the NEW number
    user.refresh_from_db()
    assert user.phone == old and user.phone_verified  # nothing is saved until the code is entered

    code = re.search(r"code is (\d{6})", out).group(1)
    wrong = "000000" if code != "000000" else "111111"
    assert c.post(reverse("verify-contact", kwargs={"kind": "phone"}), {"code": wrong}).status_code == 200
    user.refresh_from_db()
    assert user.phone == old
    assert c.post(reverse("verify-contact", kwargs={"kind": "phone"}), {"code": code}).status_code == 302
    user.refresh_from_db()
    assert user.phone == "255700000099" and user.phone_verified


@pytest.mark.django_db
def test_profile_phone_unchanged_or_taken_does_not_start_a_change(places, capsys):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    student = _student(places)
    user, old = student.user, student.user.phone
    c = _client(user)

    # The same number typed with a "+" is not a change.
    same = c.post(reverse("student-profile"), _profile_post(places, "+" + old))
    assert same.status_code == 302 and same.url == reverse("student-dashboard")
    assert "[SMS:MOCK]" not in capsys.readouterr().out

    # A number another account already uses is refused with the agreed message, and no code is sent.
    User.objects.create_user(email="other@x.tz", password="Secret123!x", role="STUDENT", phone="255700000077")
    taken = c.post(reverse("student-profile"), _profile_post(places, "255700000077"))
    assert taken.status_code == 200
    assert "This phone number is already associated with an account." in taken.content.decode()
    assert "[SMS:MOCK]" not in capsys.readouterr().out
    user.refresh_from_db()
    assert user.phone == old


@pytest.mark.django_db
def test_first_phone_in_the_profile_is_saved_directly(places):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    student = _student(places)
    User.objects.filter(pk=student.user_id).update(phone="", phone_verified=False)
    resp = _client(student.user).post(reverse("student-profile"), _profile_post(places, "255700000099"))
    assert resp.status_code == 302 and resp.url == reverse("student-dashboard")
    student.user.refresh_from_db()
    assert student.user.phone == "255700000099" and not student.user.phone_verified  # still to be verified


@pytest.mark.django_db
def test_admin_edit_form_still_writes_the_phone_directly(places):
    student = _student(places)
    form = StudentProfileForm(
        _profile_post(places, "255700000055"), instance=student, user=student.user, require_phone=False
    )
    assert form.is_valid(), form.errors
    form.save(commit=False)
    student.user.refresh_from_db()
    assert student.user.phone == "255700000055" and form.pending_phone is None


# ── password: a rejected change is shown inside the security tab ────────────

@pytest.mark.django_db
def test_rejected_new_password_is_shown_in_the_security_tab(places):
    student = _student(places)
    c = _client(student.user)
    bad = c.post(
        "/auth/change-password/",
        {"current_password": "wrong-one", "password1": "NewSecret123!x", "password2": "NewSecret123!x"},
    )
    assert bad.status_code == 302 and bad.url == reverse("student-profile") + "#security"
    page = c.get(reverse("student-profile"))
    assert "current password is incorrect" in page.content.decode()
    student.user.refresh_from_db()
    assert student.user.check_password("Secret123!x")

    weak = c.post(
        "/auth/change-password/",
        {"current_password": "Secret123!x", "password1": "abc", "password2": "abc"},
    )
    assert weak.status_code == 302 and weak.url == reverse("student-profile") + "#security"

    ok = c.post(
        "/auth/change-password/",
        {"current_password": "Secret123!x", "password1": "NewSecret123!x", "password2": "NewSecret123!x"},
    )
    assert ok.status_code == 302 and ok.url != reverse("student-profile") + "#security"
    student.user.refresh_from_db()
    assert student.user.check_password("NewSecret123!x")


@pytest.mark.django_db
def test_forced_password_change_keeps_its_own_page(places):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    student = _student(places)
    User.objects.filter(pk=student.user_id).update(must_change_password=True)
    resp = _client(student.user).post(
        "/auth/change-password/",
        {"current_password": "wrong-one", "password1": "NewSecret123!x", "password2": "NewSecret123!x"},
    )
    assert resp.status_code == 200 and "Choose a new password" in resp.content.decode()
