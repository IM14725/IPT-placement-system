"""Phone fixes for the organization slot and applicant pages (review of 2026-11-16)."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import Document
from apps.documents.tests import make_png_bytes
from apps.students.test_dashboard_features import (  # noqa: F401
    _client,
    _pay,
    _reset_redis_state,
    _slot,
    _student,
    org,
    places,
)


@pytest.fixture
def ready_org(org):
    """The shared test organization with the three required documents, so no page redirects to the profile."""
    for doc_type in (Document.DocType.BRELA_CERT, Document.DocType.TIN_CERT, Document.DocType.BUSINESS_LICENSE):
        Document.objects.create(
            owner=org.user, doc_type=doc_type, original_name="c.png",
            file=SimpleUploadedFile("c.png", make_png_bytes(), content_type="image/png"),
        )
    return org


def _applicants_page(ready_org, places, slot, n=1):
    for i in range(n):
        _pay(_student(places, email=f"s{i}@x.tz"), slot)
    return _client(ready_org.user).get(reverse("organization-applicants", args=[slot.id])).content.decode()


# ── 1. "80% Filled", not "80%% Filled" ──────────────────────────────────────────

@pytest.mark.django_db
def test_slot_applicants_capacity_reads_one_percent_sign(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=5)
    body = _applicants_page(ready_org, places, slot, n=4)
    assert "80% Filled" in body and "%%" not in body


@pytest.mark.django_db
def test_slot_applicants_capacity_percent_in_kiswahili(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=5)
    for i in range(4):
        _pay(_student(places, email=f"s{i}@x.tz"), slot)
    c = _client(ready_org.user)
    c.cookies["django_language"] = "sw"
    body = c.get(reverse("organization-applicants", args=[slot.id])).content.decode()
    assert "80% Imejaa" in body and "%%" not in body


# ── 2. stipend reads "TZS 300,000 / month" ─────────────────────────────────────

def test_money_filter():
    from apps.core.templatetags.money_tags import money

    assert money("300000.00") == "300,000"
    assert money(1250000) == "1,250,000"
    assert money(None) is None


@pytest.mark.django_db
def test_stipend_is_shown_as_whole_shillings_with_separator(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Paid intern", capacity=2, stipend_available=True, stipend_amount=300000)
    mine = _client(ready_org.user).get(reverse("organization-slots")).content.decode()
    assert "TZS 300,000" in mine and "300000" not in mine and ".00" not in mine
    assert "/ month" in mine and "/mo<" not in mine
    student = _client(_student(places).user)
    assert "TZS 300,000 / month" in student.get(reverse("student-slot-detail", args=[slot.id])).content.decode()
    apply_page = student.get(reverse("student-apply", args=[slot.id])).content.decode()
    assert "TZS 300,000 / month" in apply_page
    sw = _client(ready_org.user)
    sw.cookies["django_language"] = "sw"
    assert "/ mwezi" in sw.get(reverse("organization-slots")).content.decode()


# ── 3. no dangling "•" between meta items ─────────────────────────────────────

@pytest.mark.django_db
def test_slot_meta_line_uses_wrap_safe_separators(ready_org, places):
    _slot(ready_org, places["kinondoni"], "Intern", capacity=2, department="Computer Science")
    body = _client(ready_org.user).get(reverse("organization-slots")).content.decode()
    assert body.count("ipt-meta-item") >= 4
    assert "•" not in body  # no hand-written bullets left between items (they dangled at wrapped line ends)
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / "static" / "css" / "responsive.css").read_text(encoding="utf-8")
    assert ".ipt-meta { overflow: hidden; }" in css and r'content: "\2022"' in css


# ── 4. slot Applicants filters fit side by side on a phone ─────────────────────

@pytest.mark.django_db
def test_slot_applicants_filters_use_short_labels(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=3)
    body = _applicants_page(ready_org, places, slot)
    assert 'class="ipt-compact-filters' in body
    for short in ("Any level", "Newest paid", "Oldest paid", ">Education level<"):
        assert short in body, short
    for long in ("Any qualification", "Newest paid first", "Earliest paid first", "Filter by Education Level"):
        assert long not in body, long


@pytest.mark.django_db
def test_slot_applicants_header_has_no_trailing_separator(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=3)
    body = _applicants_page(ready_org, places, slot)
    head = body[body.index("<h1") : body.index("</section>", body.index("<h1"))]
    assert head.count("ipt-meta-item") == 3 and "w-1 h-1 rounded-full" not in head


# ── 5. My Slots: two labelled buttons + a "More" menu with text labels ─────────

@pytest.mark.django_db
def test_my_slots_actions_are_labelled_with_a_more_menu(ready_org, places):
    empty = _slot(ready_org, places["kinondoni"], "No applicants yet", capacity=2)
    busy = _slot(ready_org, places["kinondoni"], "Has applicants", capacity=2)
    _pay(_student(places), busy)
    c = _client(ready_org.user)
    body = c.get(reverse("organization-slots")).content.decode()
    assert body.count('class="relative ipt-more"') == 2
    for label in ("Applicants", "Edit", "More", "Pause", "Extend deadline", "Close to new applications", "Duplicate (post again)"):
        assert f">{label}<" in body.replace("</span>", "<").replace("\n", "") or label in body, label
    assert "p-1.5 text-on-surface-variant" not in body  # the unlabelled icon buttons are gone
    # Delete is the last item of the menu, red, with its confirm; a slot with applications explains why it cannot be deleted.
    delete_url = reverse("organization-slot-delete", args=[empty.id])
    assert f'action="{delete_url}"' in body and "return confirm(" in body
    assert reverse("organization-slot-delete", args=[busy.id]) not in body
    assert body.count("cannot be deleted") == 1
    # The actions still work: pause through the menu's form.
    assert c.post(reverse("organization-slot-toggle", args=[empty.id])).status_code == 302
    empty.refresh_from_db()
    assert empty.status == "PAUSED"


# ── 6/7. slot title on at most two lines; the spots-left bar spans the card on phones ──

@pytest.mark.django_db
def test_slot_card_title_wraps_and_the_bar_is_full_width_on_phones(ready_org, places):
    _slot(ready_org, places["kinondoni"], "A very long slot title for the Dar es Salaam head office internship", capacity=4)
    body = _client(ready_org.user).get(reverse("organization-slots")).content.decode()
    assert "line-clamp-2" in body and 'text-on-surface truncate">' not in body
    assert 'title="A very long slot title' in body  # the full title stays available on hover/long-press
    assert "flex-1 sm:flex-none sm:w-24" in body and "w-16 h-1.5" not in body


# ── 8/9. slot Applicants: trimmed cards with Documents / Notes / Accept, stats summary on top ──

@pytest.mark.django_db
def test_slot_applicants_uses_the_compact_rows_and_a_stats_summary(ready_org, places):
    from apps.applications.services import accept_and_notify

    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=5)
    apps = [_pay(_student(places, email=f"s{i}@x.tz"), slot) for i in range(3)]
    accept_and_notify(apps[0], company_message="Welcome")
    body = _client(ready_org.user).get(reverse("organization-applicants", args=[slot.id])).content.decode()

    # one compact row per applicant; the old tall card (notes box + note field + big button) is gone
    assert body.count(reverse("organization-applicant-documents", args=[apps[1].id])) == 1
    assert "Private notes" not in body and "One click automatically sends" not in body
    assert "w-1 h-1 rounded-full bg-outline-variant" not in body  # no dangling dot separators in the details
    # Notes / Accept / Documents buttons exist per applicant; Accept opens the note box, accepted ones get the letter link
    assert reverse("organization-applicant-notes", args=[apps[1].id]) in body
    assert f'action="{reverse("organization-acceptance-sms", args=[apps[1].id])}"' in body
    assert f'action="{reverse("organization-acceptance-sms", args=[apps[0].id])}"' not in body  # already accepted
    assert reverse("organization-applicant-acceptance-letter", args=[apps[0].id]) in body
    assert "GPA" not in body or "· GPA" in body  # GPA shows in the details line when the student has one
    # the slot's own title is not repeated on every row
    assert body.count(">Intern<") <= 2

    # phones: a one-line summary and a collapsed "Slot statistics" section ABOVE the list; desktop keeps the sidebar
    summary = body.index('class="lg:hidden flex flex-col gap-sm"')
    first_row = body.index(reverse("organization-applicant-documents", args=[apps[1].id]))
    assert summary < first_row
    section = body[summary:first_row]
    assert "Slot statistics" in section and "<details" in section and "Slot Overview" in section
    assert 'class="hidden lg:flex lg:col-span-4' in body
    assert "1</strong> Accepted" in section and "2</strong> Pending Review" in section


@pytest.mark.django_db
def test_slot_applicants_cooling_off_applicant_cannot_be_accepted_from_the_row(ready_org, places):
    from apps.applications.models import Application

    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=2)
    app = _pay(_student(places), slot)
    Application.objects.filter(pk=app.pk).update(can_cancel=True)  # still inside the 24 h window
    body = _client(ready_org.user).get(reverse("organization-applicants", args=[slot.id])).content.decode()
    assert "In 24h cancellation window" in body
    assert f'action="{reverse("organization-acceptance-sms", args=[app.id])}"' not in body


# ── 10. every Accept asks first, naming the student ───────────────────────────

@pytest.mark.django_db
def test_accept_buttons_ask_for_confirmation_with_the_students_name(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=3)
    app = _pay(_student(places), slot)
    c = _client(ready_org.user)
    for url in (reverse("organization-applicants", args=[slot.id]), reverse("organization-all-applicants"), reverse("organization-dashboard")):
        body = c.get(url).content.decode()
        form = body[body.index(f'action="{reverse("organization-acceptance-sms", args=[app.id])}"') :][:400]
        assert "return confirm(" in form and "Asha Juma" in form and "cannot be undone" in form, url
    bulk = c.get(reverse("organization-all-applicants")).content.decode()
    assert "data-confirm=" in bulk and "__N__" in bulk and "data-none=" in bulk
    # Kiswahili asks too
    c.cookies["django_language"] = "sw"
    sw = c.get(reverse("organization-applicants", args=[slot.id])).content.decode()
    assert "Kubali Asha Juma?" in sw


# ── 11. All applicants: search on its own row, Filters + Export below, "Apply filters" in the panel ──

@pytest.mark.django_db
def test_all_applicants_search_row_layout(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=2)
    _pay(_student(places), slot)
    body = _client(ready_org.user).get(reverse("organization-all-applicants")).content.decode()
    bar = body[body.index('data-filterbar'): body.index('data-filter-chips')]
    assert bar.index('id="f-q"') < bar.index("data-filter-toggle") < bar.index("Export CSV")  # search, then Filters, then Export
    assert 'aria-label="Search" class="order-2 md:order-3' in bar  # the search submit is an icon button
    assert bar.count("data-filter-toggle") == 1  # filterbar.js binds a single toggle
    assert ">Filter<" in bar  # still labelled on wide screens
    assert "placeholder=\"Search applicants\"" in bar  # short enough not to be clipped on a phone
    panel = body[body.index('id="applicant-filter-panel"'):][:3500]
    assert "Apply filters" in panel
    export_links = [m for m in body.split("<a ") if "organization/applicants/export" in m.split(">")[0]]
    assert len(export_links) == 2  # header link (wide screens) + the one beside Filters (phones)


# ── 12. bulk accept is a bottom bar that only shows once someone is ticked ─────

@pytest.mark.django_db
def test_bulk_accept_is_a_hidden_bar_with_select_all_in_the_list_heading(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=4)
    for i in range(3):
        _pay(_student(places, email=f"s{i}@x.tz"), slot)
    body = _client(ready_org.user).get(reverse("organization-all-applicants")).content.decode()
    form_start = body.index('id="bulk-accept-form"')
    bar = body[form_start : body.index("</form>", form_start)]
    assert "data-bulk-bar" in bar and 'class="hidden fixed inset-x-0 bottom-0' in bar  # hidden until a box is ticked
    assert "<details>" in bar and 'id="bulk-message"' in bar and 'id="bulk-template"' in bar  # message fields open from the bar
    assert "data-bulk-count" in bar and "Accept selected" in bar
    assert "data-confirm=" in bar and "data-none=" in bar  # the safety step from fix 10 is kept
    # "Select all on this page" lives with the list heading, before the first row
    assert body.index('id="select-all"') < body.index('type="checkbox" name="application"')
    assert "data-bulk-spacer" in body
    assert body.count('type="checkbox" name="application"') == 3 and body.count('value="') and body.count('" form="bulk-accept-form"') == 3
