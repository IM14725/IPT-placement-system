"""Tier 3: role entry points, FAQ + WhatsApp button, and the cookie-less signup funnel."""

import pytest
from django.test import Client

from apps.accounts.models import User, UserRole
from apps.accounts.tasks import process_registration_task
from apps.core.funnel import funnel_summary, is_bot, track

STRONG = "Zx9!kQ4#vTe2"


@pytest.fixture(autouse=True)
def _counters_on(settings):
    settings.CACHE_ENABLED = True  # the funnel lives in (fake) Redis; assertions compare before/after
    from apps.accounts.public import CACHE_KEY
    from apps.core.cache import cache_delete

    cache_delete(CACHE_KEY)  # the welcome data is cached for 5 minutes; each test needs its own fee/slots


def _total(key):
    return next(row["total"] for row in funnel_summary(days=2) if row["key"] == key)


def _task_data(**over):
    data = {
        "email": "f@example.tz", "password_hash": "pbkdf2_sha256$1$x$y", "role": "STUDENT",
        "first_name": "A", "last_name": "B", "phone": "",
    }
    data.update(over)
    return data


# ------------------------------------------------------------------ #16 role entry points
@pytest.mark.django_db
@pytest.mark.parametrize("param,value", [("student", "STUDENT"), ("organization", "ORGANIZATION"), ("STUDENT", "STUDENT")])
def test_register_preselects_the_role_from_the_link(client, param, value):
    html = client.get(f"/auth/register/?role={param}").content.decode()
    assert "data-preselected" in html
    checked = [line for line in html.splitlines() if f'value="{value}"' in line and "checked" in line]
    assert checked, "the role radio should be ticked"


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/auth/register/", "/auth/register/?role=admin", "/auth/register/?role="])
def test_unknown_or_missing_role_preselects_nothing(client, url):
    html = client.get(url).content.decode()
    assert "data-preselected" not in html
    assert 'value="ADMIN"' not in html


@pytest.mark.django_db
def test_a_rejected_post_does_not_skip_the_role_step(client):
    html = client.post("/auth/register/?role=student", {"role": "STUDENT", "email": "bad"}).content.decode()
    assert "data-preselected" not in html


@pytest.mark.django_db
def test_welcome_and_login_pages_offer_both_entry_buttons(client):
    for url in ("/", "/auth/login/"):
        html = client.get(url).content.decode()
        assert "/auth/register/?role=student" in html and "/auth/register/?role=organization" in html
    assert client.get("/").content.decode().count("/auth/register/?role=student") >= 2  # hero + closing banner


# ------------------------------------------------------------------ #18 FAQ + WhatsApp
@pytest.mark.django_db
def test_welcome_page_has_the_faq_with_the_live_fee(client, settings):
    settings.APPLICATION_FEE = 20000
    html = client.get("/").content.decode()
    assert 'id="faq"' in html and html.count("<details") >= 9
    assert "TZS 20,000 for each application" in html
    assert "#payments" in html


@pytest.mark.django_db
def test_whatsapp_button_shows_for_visitors_only(client):
    html = client.get("/auth/login/").content.decode()
    assert "https://wa.me/255756322833?text=" in html
    user = User.objects.create_user(email="w@example.tz", password=STRONG, role=UserRole.STUDENT)
    user.email_verified = True
    user.save(update_fields=["email_verified"])
    client.force_login(user)
    assert 'class="no-print fixed left-3' not in client.get("/auth/account/").content.decode()


@pytest.mark.django_db
def test_faq_and_whatsapp_in_kiswahili(client):
    client.cookies["django_language"] = "sw"
    html = client.get("/").content.decode()
    assert "Maswali yanayoulizwa mara kwa mara" in html
    assert "Mimi ni mwanafunzi" in html
    assert "Ongea nasi kwenye WhatsApp" in html


# ------------------------------------------------------------------ #20 funnel
@pytest.mark.django_db
def test_visits_are_counted_but_crawlers_are_not(client):
    before = (_total("home"), _total("register_view"))
    client.get("/")
    client.get("/auth/register/")
    assert (_total("home"), _total("register_view")) == (before[0] + 1, before[1] + 1)
    client.get("/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; Googlebot/2.1)")
    assert _total("home") == before[0] + 1


@pytest.mark.django_db
def test_signed_in_visitors_do_not_inflate_the_welcome_count(client):
    user = User.objects.create_user(email="in@example.tz", password=STRONG, role=UserRole.STUDENT)
    client.force_login(user)
    before = _total("home")
    client.get("/")
    assert _total("home") == before


def test_bot_detection():
    class R:
        def __init__(self, ua): self.META = {"HTTP_USER_AGENT": ua}
    assert is_bot(R("Googlebot/2.1")) and is_bot(R("curl/8.0")) and is_bot(R("HeadlessChrome"))
    assert not is_bot(R("Mozilla/5.0 (Linux; Android 13) Chrome/120 Mobile Safari/537.36"))


@pytest.mark.django_db
def test_wizard_beacon_counts_whitelisted_steps_without_csrf_or_cookies():
    anon = Client(enforce_csrf_checks=True)
    before = _total("register_step2")
    assert anon.post("/auth/funnel/", {"step": "register_step2"}).status_code == 204
    assert _total("register_step2") == before + 1
    assert not anon.cookies.get("sessionid")


@pytest.mark.django_db
@pytest.mark.parametrize("step", ["", "home", "account_created", "register_submit", "evil"])
def test_beacon_cannot_inflate_server_side_steps(client, step):
    before = {row["key"]: row["total"] for row in funnel_summary(days=2)}
    assert client.post("/auth/funnel/", {"step": step}).status_code == 400
    assert {row["key"]: row["total"] for row in funnel_summary(days=2)} == before


@pytest.mark.django_db
def test_beacon_is_post_only(client):
    assert client.get("/auth/funnel/").status_code == 405


@pytest.mark.django_db
def test_only_real_accounts_count_as_created():
    before = _total("account_created")
    assert process_registration_task(_task_data(email="new1@example.tz"))["status"] == "success"
    assert _total("account_created") == before + 1
    # an existing address (decoy outcome) is not a new account
    process_registration_task(_task_data(email="new1@example.tz"))
    assert _total("account_created") == before + 1


@pytest.mark.django_db
def test_submitting_a_valid_form_counts_once(client):
    before = _total("register_submit")
    client.post(
        "/auth/register/",
        {"role": "STUDENT", "email": "sub@example.tz", "first_name": "A", "last_name": "B",
         "password1": STRONG, "password2": STRONG, "accept_terms": "on"},
    )
    assert _total("register_submit") == before + 1
    client.post("/auth/register/", {"role": "STUDENT", "email": "not-an-email"})  # invalid: not a submission
    assert _total("register_submit") == before + 1


def test_unknown_steps_are_ignored():
    before = [row["total"] for row in funnel_summary(days=2)]
    track("made_up")
    assert [row["total"] for row in funnel_summary(days=2)] == before


def test_summary_conversion_maths(monkeypatch):
    values = {"home": 200, "register_view": 100, "register_step2": 50, "register_step3": 25, "register_submit": 10, "account_created": 5}
    monkeypatch.setattr(
        "apps.core.funnel.daily_series",
        lambda prefix, days=30: [{"date": "2026-11-11", "value": values[prefix.split(":", 1)[1]]}],
    )
    rows = funnel_summary()
    assert [r["total"] for r in rows] == [200, 100, 50, 25, 10, 5]
    assert rows[0]["of_previous"] is None and rows[0]["of_first"] == 100.0
    assert rows[1]["of_previous"] == 50.0 and rows[5]["of_first"] == 2.5


@pytest.mark.django_db
def test_metrics_data_includes_the_funnel():
    from apps.core.views import _compute_metrics

    funnel = _compute_metrics()["funnel"]
    assert [row["key"] for row in funnel][0] == "home" and len(funnel) == 6
