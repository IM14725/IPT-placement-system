"""Admin Metrics charts (ranges, shapes, local Chart.js) and the single-input-style guard."""

import glob
import os
import re

import pytest
from django.conf import settings
from django.test import Client

from apps.accounts.models import User, UserRole
from apps.core.metrics import RANGES, build_charts, normalise_range, periods

EXPECTED_CHARTS = {
    "revenue", "signups", "applications", "logins", "searches",
    "slots_status", "verification", "payments_status", "payments_method",
    "slots_region", "slots_district", "applied_region", "applied_district", "top_organizations",
}


@pytest.fixture
def admin(db):
    user = User.objects.create_superuser(email="super@metrics.tz", password="Secret123!x")
    c = Client()
    c.force_login(user)
    return c


def test_range_values_are_whitelisted():
    assert [normalise_range(r) for r in ("7", "30", "90", "all", "bogus", None, "1; drop")] == ["7", "30", "90", "all", "30", "30", "30"]


@pytest.mark.django_db
@pytest.mark.parametrize("range_key,points", [("7", 7), ("30", 30), ("90", 90)])
def test_daily_ranges_have_one_point_per_day(range_key, points):
    dates, monthly = periods(range_key)
    assert len(dates) == points and monthly is False


@pytest.mark.django_db
def test_all_time_is_monthly_and_ends_this_month():
    from django.utils import timezone

    dates, monthly = periods("all")
    assert monthly is True and dates[-1] == timezone.localdate().replace(day=1)
    assert all(d.day == 1 for d in dates) and len(dates) <= 36


@pytest.mark.django_db
@pytest.mark.parametrize("range_key", RANGES)
def test_every_chart_is_present_and_well_formed(range_key):
    data = build_charts(range_key)
    charts = data["all"]
    assert {c["id"] for c in charts} == EXPECTED_CHARTS
    for c in charts:
        assert c["shape"] in ("series", "doughnut", "bars") and c["title"] and "ranged" in c
        if c["shape"] == "series":
            assert all(len(d["data"]) == len(c["labels"]) for d in c["datasets"])
        elif c["shape"] == "doughnut":
            assert len(c["labels"]) == len(c["data"]) == len(c["colors"])
        else:
            assert all(len(i["values"]) == len(c["datasets"]) for i in c["items"])


@pytest.mark.django_db
def test_signups_chart_counts_new_students_and_organizations():
    User.objects.create_user(email="a@x.tz", password="Secret123!x", role=UserRole.STUDENT)
    User.objects.create_user(email="b@x.tz", password="Secret123!x", role=UserRole.STUDENT)
    User.objects.create_user(email="c@x.tz", password="Secret123!x", role=UserRole.ORGANIZATION)
    chart = next(c for c in build_charts("7")["all"] if c["id"] == "signups")
    totals = {d["label"]: sum(d["data"]) for d in chart["datasets"]}
    assert totals == {"Students": 2, "Organizations": 1}


@pytest.mark.django_db
def test_metrics_page_renders_for_every_range_with_local_chartjs(admin):
    for range_key in (*RANGES, "bogus"):
        response = admin.get(f"/platform/metrics/?range={range_key}")
        html = response.content.decode()
        assert response.status_code == 200
        assert "cdn.jsdelivr" not in html and "vendor/chartjs/chart.umd.min.js" in html and "js/metrics-charts.js" in html
        assert 'id="charts-data"' in html and "No data in this period." in html
    assert "aria-current" in admin.get("/platform/metrics/?range=90").content.decode()


@pytest.mark.django_db
def test_metrics_page_is_admin_only(client):
    student = User.objects.create_user(email="s@metrics.tz", password="Secret123!x", role=UserRole.STUDENT)
    client.force_login(student)
    assert client.get("/platform/metrics/").status_code == 403


def test_chartjs_is_served_from_our_own_static_files():
    path = os.path.join(settings.BASE_DIR, "static", "vendor", "chartjs", "chart.umd.min.js")
    assert os.path.getsize(path) > 100_000


# ------------------------------------------------------------------ one input style
BOX = ("ipt-field-box", "ipt-combobox", "ipt-file-wrap")
SKIP_TYPES = {"hidden", "checkbox", "radio", "submit", "button", "reset", "image", "color", "range", "file"}
ALLOWED = {("base.html", "language")}  # the language switcher lives on the coloured top bar
TAG = re.compile(r"<(/?)div\b([^>]*)>|<(input|select|textarea)\b([^>]*?)/?>", re.S | re.I)


def test_no_raw_form_control_sits_outside_an_icon_chip_box():
    root = os.path.join(settings.BASE_DIR, "templates")
    offenders = []
    for path in glob.glob(os.path.join(root, "**", "*.html"), recursive=True):
        rel = os.path.relpath(path, root).replace("\\", "/")
        src = open(path, encoding="utf-8").read()
        stack = []
        for m in TAG.finditer(src):
            text = m.group(0).lower()
            if text.startswith("</div"):
                if stack:
                    stack.pop()
            elif text.startswith("<div"):
                if not (m.group(2) or "").rstrip().endswith("/"):
                    stack.append(any(b in (m.group(2) or "") for b in BOX))
            else:
                attrs = m.group(4) or ""
                t = re.search(r'\btype="([^"]+)"', attrs)
                typ = t.group(1).lower() if t else "text"
                ident = re.search(r'\b(?:name|id)="([^"]+)"', attrs)
                ident = ident.group(1) if ident else ""
                if typ in SKIP_TYPES or any(stack) or (rel, ident) in ALLOWED or rel == "base.html":
                    continue
                offenders.append(f"{rel}:{src.count(chr(10), 0, m.start()) + 1} <{m.group(3)} {ident}>")
    assert not offenders, "wrap these in an .ipt-field-box (or use includes/form_field.html):\n" + "\n".join(offenders)


@pytest.mark.django_db
def test_form_field_include_draws_the_chip_style(client):
    html = client.get("/auth/forgot-password/").content.decode()
    assert "ipt-field-box" in html
    from django import forms
    from django.template.loader import render_to_string

    class F(forms.Form):
        email = forms.EmailField()
        agree = forms.BooleanField()

    form = F(data={"email": "bad"})
    form.is_valid()
    out = render_to_string("includes/form_field.html", {"field": form["email"]})
    assert "ipt-field-box is-error" in out and ">mail<" in out and "ipt-field-error" in out
    checkbox = render_to_string("includes/form_field.html", {"field": form["agree"]})
    assert "ipt-field-box" not in checkbox and 'type="checkbox"' in checkbox
