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


# ── 13. row checkbox: 20 px box inside a 44 px-high label that also covers the avatar ─────

@pytest.mark.django_db
def test_applicant_row_checkbox_has_a_big_tap_area(ready_org, places):
    slot = _slot(ready_org, places["kinondoni"], "Intern", capacity=2)
    _pay(_student(places), slot)
    c = _client(ready_org.user)
    body = c.get(reverse("organization-all-applicants")).content.decode()
    label = body[body.index('<label class="flex items-start gap-sm shrink-0 min-h-[44px] cursor-pointer">') :][:900]
    assert 'class="w-5 h-5' in label and "ipt-img" in label or "bg-primary/10" in label  # the avatar sits inside the same label
    assert "</label>" in label and label.index('type="checkbox"') < label.index("</label>")
    # rows that are not selectable (dashboard, slot page) have no label wrapper or checkbox
    for url in (reverse("organization-dashboard"), reverse("organization-applicants", args=[slot.id])):
        page = c.get(url).content.decode()
        assert 'type="checkbox" name="application"' not in page


# ── 14-17. the slot form: sections, stipend row, role type hints, date echo, Cancel button ─────

@pytest.mark.django_db
def test_slot_form_is_split_into_sections_and_loses_no_field(ready_org, places):
    import re

    from apps.companies.forms import SlotForm

    c = _client(ready_org.user)
    slot = _slot(ready_org, places["kinondoni"], "Existing", capacity=2)
    for url in (reverse("organization-slot-create"), reverse("organization-slot-edit", args=[slot.id])):
        body = c.get(url).content.decode()
        sections = re.findall(r'<details class="ipt-section"( open)?>', body)
        assert len(sections) == 5 and sections.count(" open") == 3, url  # Basics, Location, Seats open; Requirements, Timing closed
        for title in ("Basics", "Location", "Requirements", "Timing", "Seats & stipend"):
            assert title in body, (url, title)
        for name in SlotForm().fields:
            if name == "district":
                assert 'id="id_district"' in body and 'name="district"' in body
            else:
                assert f'name="{name}"' in body, (url, name)
        assert 'id="id_region"' in body and "form-sections.js" in body


@pytest.mark.django_db
def test_slot_form_hints_stipend_row_dates_and_cancel_button(ready_org, places):
    body = _client(ready_org.user).get(reverse("organization-slot-create")).content.decode()
    # 16. examples in the required fields and role type suggestions that still allow your own text
    assert 'placeholder="e.g. Software Engineering Intern"' in body and 'placeholder="e.g. Internship"' in body
    assert 'list="role-type-options"' in body and '<datalist id="role-type-options">' in body
    for suggestion in ("Internship", "Attachment", "Field placement", "Other"):
        assert f'<option value="{suggestion}">' in body
    # 15. the stipend amount sits in its own row, found by the script (it used to look for it around the checkbox)
    assert body.count("data-stipend-row") >= 2  # the row and the script's selector
    assert "closest('[data-stipend-row]')" not in body and "document.querySelector('[data-stipend-row]')" in body
    # 17. dates are echoed as text; Cancel is a 48 px outlined button
    assert body.count("data-date-for=") == 2 and 'data-prefix="Closes"' in body and 'data-prefix="Starts"' in body
    assert "min-h-[48px] px-lg inline-flex items-center rounded-full border" in body
    assert "font-label-caps text-label-caps text-primary hover:text-primary/80" not in body


@pytest.mark.django_db
def test_slot_form_still_posts_a_new_slot_and_clears_the_stipend_when_unticked(ready_org, places):
    c = _client(ready_org.user)
    resp = c.post(
        reverse("organization-slot-create"),
        {"title": "Intern", "role_type": "Internship", "region": places["dar"].id, "district": places["kinondoni"].id, "capacity": 2,
         "stipend_amount": "250000", "industry": "", "description": "", "department": "", "education_level": "",
         "academic_year": "", "application_deadline": "", "start_date": "", "duration_weeks": "",
         "reporting_contact": "", "skills_required": ""},
    )
    assert resp.status_code == 302
    from apps.slots.models import Slot

    created = Slot.objects.get(title="Intern")
    assert created.stipend_available is False and created.stipend_amount is None  # untick means no stipend
