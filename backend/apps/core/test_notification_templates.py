"""Admin notification templates: preloaded built-ins, create/duplicate/edit/delete, validation, test send."""

import pytest
from django.test import Client

from apps.accounts.models import User, UserRole
from apps.core.models import AuditLog
from apps.notifications import events as ev
from apps.notifications.models import MessageChannel, NotificationTemplate
from apps.notifications.services import render_template

URL = "/platform/templates/"


@pytest.fixture
def admin(db):
    user = User.objects.create_superuser(email="super@templates.tz", password="Secret123!x")
    user.phone = "255712000111"
    user.save(update_fields=["phone"])
    c = Client()
    c.force_login(user)
    return c, user


def _post(c, **data):
    return c.post(URL, data)


# ------------------------------------------------------------------ access
@pytest.mark.django_db
def test_only_super_admins_can_open_the_page(client):
    student = User.objects.create_user(email="s@templates.tz", password="Secret123!x", role=UserRole.STUDENT)
    client.force_login(student)
    assert client.get(URL).status_code == 403
    assert client.post(URL, {"action": "delete", "key": "receipt_email"}).status_code == 403
    assert NotificationTemplate.objects.filter(key="receipt_email").exists()


# ------------------------------------------------------------------ built-ins
@pytest.mark.django_db
def test_the_four_system_messages_are_preloaded(admin):
    c, _ = admin
    assert set(NotificationTemplate.objects.values_list("key", flat=True)) >= set(ev.EVENTS)
    html = c.get(URL).content.decode()
    for event in ev.EVENTS.values():
        assert event.label in html


def _fallback(event_key, ctx):
    """The wording the senders use when no template exists (copied from apps/*/tasks.py)."""
    if event_key == "payment_received_sms":
        return "", (f"Application Sent! App ID: #{ctx['app_id']} | Organization: {ctx['organization_name']} | "
                    f"Slot: {ctx['slot_title']}. Track it on the IPT Marketplace.")
    if event_key == "acceptance_sms":
        return "", (f"Congratulations! You have been accepted for {ctx['slot_title']} at {ctx['organization_name']}. "
                    "Check your email for the acceptance letter or further instructions.")
    if event_key == "acceptance_email":
        return (f"Congratulations! You have been accepted — {ctx['slot_title']}",
                f"Dear {ctx['student_name']},\n\n"
                f"Congratulations! Your application (ID: #{ctx['app_id']}) for '{ctx['slot_title']}' at {ctx['organization_name']} has been accepted.\n\n"
                "Please open your email and check for the official acceptance letter and further instructions from the organization. "
                f"Contact {ctx['organization_name']} if you have any questions.{ctx['company_note']}\n\n"
                f"Best regards,\n{ctx['organization_name']}")
    return (f"Payment Receipt - {ctx['reference_id']}",
            f"Dear {ctx['student_name']},\n\n"
            f"Thank you for your application to {ctx['organization_name']} ({ctx['slot_title']}). "
            f"Your application fee of {ctx['amount']} {ctx['currency']} has been received.\n\n"
            f"Reference: {ctx['reference_id']}\nPaid on: {ctx['paid_at']}\n\n"
            "Your application has been submitted and accepted. Please see the attached receipt.\n\nIPT Marketplace")


@pytest.mark.parametrize("key", list(ev.EVENTS))
def test_preloaded_wording_is_identical_to_the_built_in_fallback(key):
    event = ev.EVENTS[key]
    assert ev.render_sample(event, event.default_subject, event.default_body) == _fallback(key, event.samples)


@pytest.mark.django_db
def test_senders_use_an_edited_template_and_fall_back_after_delete(admin):
    c, _ = admin
    _post(c, action="save", key="acceptance_sms", name="Accepted", body="Hi {student_name}, you got {slot_title}!", is_active="on")
    ctx = {"student_name": "Asha", "slot_title": "Intern", "organization_name": "Acme"}
    assert render_template("acceptance_sms", MessageChannel.SMS, ctx, fallback_body="FALLBACK")[1] == "Hi Asha, you got Intern!"
    _post(c, action="delete", key="acceptance_sms")
    assert render_template("acceptance_sms", MessageChannel.SMS, ctx, fallback_body="FALLBACK")[1] == "FALLBACK"


# ------------------------------------------------------------------ create / duplicate
@pytest.mark.django_db
def test_new_template_is_offered_only_for_events_without_one(admin):
    c, _ = admin
    NotificationTemplate.objects.filter(key="receipt_email").delete()
    html = c.get(URL + "?new=1").content.decode()
    assert "Payment receipt (email)" in html and "Create template" in html
    assert 'value="receipt_email" selected' in html
    picker = html.split('id="pick-event"')[1].split("</select>")[0]
    assert 'value="acceptance_sms"' not in picker  # already has a template, so it is not offered


@pytest.mark.django_db
def test_create_makes_a_template_for_the_event_with_the_events_channel(admin):
    c, _ = admin
    NotificationTemplate.objects.filter(key="acceptance_sms").delete()
    response = _post(c, action="create", key="acceptance_sms", name="My SMS", channel="EMAIL", subject="ignored",
                     body="Well done {student_name}!", is_active="on")
    assert response.status_code == 302
    t = NotificationTemplate.objects.get(key="acceptance_sms")
    assert (t.channel, t.subject, t.body, t.is_active) == ("SMS", "", "Well done {student_name}!", True)
    assert AuditLog.objects.filter(action="Template Saved").exists()


@pytest.mark.django_db
def test_cannot_create_for_an_unknown_event_or_one_that_already_has_a_template(admin):
    c, _ = admin
    before = NotificationTemplate.objects.count()
    assert _post(c, action="create", key="made_up", name="x", body="x").status_code == 302
    assert _post(c, action="create", key="receipt_email", name="dup", body="x").status_code == 302
    assert NotificationTemplate.objects.count() == before
    assert NotificationTemplate.objects.get(key="receipt_email").name != "dup"


@pytest.mark.django_db
def test_duplicate_prefills_the_form_from_another_template(admin):
    c, _ = admin
    NotificationTemplate.objects.filter(key="receipt_email").delete()
    NotificationTemplate.objects.filter(key="acceptance_email").update(body="COPIED {student_name} WORDING")
    html = c.get(URL + "?new=1&event=receipt_email&copy=acceptance_email").content.decode()
    assert "COPIED {student_name} WORDING" in html
    assert "Duplicate" in c.get(URL + "?key=acceptance_email").content.decode()


# ------------------------------------------------------------------ validation
@pytest.mark.django_db
@pytest.mark.parametrize(
    "body,needle",
    [
        ("Hello {nope}", "unknown variable"),
        ("Hello {student_name.__class__}", "invalid placeholder"),
        ("Hello {student_name[0]}", "invalid placeholder"),
        ("Hello {student_name!r}", "invalid placeholder"),
        ("Hello {student_name:>10}", "invalid placeholder"),
        ("Hello {student_name", "unmatched brace"),
        ("   ", "cannot be empty"),
    ],
)
def test_bad_text_is_rejected_and_nothing_is_saved(admin, body, needle):
    c, _ = admin
    original = NotificationTemplate.objects.get(key="acceptance_sms").body
    response = _post(c, action="save", key="acceptance_sms", name="x", body=body)
    assert response.status_code == 200 and needle in response.content.decode()
    assert NotificationTemplate.objects.get(key="acceptance_sms").body == original


@pytest.mark.django_db
def test_literal_braces_can_be_escaped(admin):
    c, _ = admin
    assert _post(c, action="save", key="acceptance_sms", name="x", body="Use {{braces}} and {student_name}").status_code == 302


@pytest.mark.django_db
def test_editing_cannot_change_the_channel_of_an_event_template(admin):
    c, _ = admin
    _post(c, action="save", key="acceptance_sms", name="x", channel="EMAIL", subject="s", body="Hi {student_name}")
    t = NotificationTemplate.objects.get(key="acceptance_sms")
    assert t.channel == "SMS" and t.subject == ""


# ------------------------------------------------------------------ delete / test send
@pytest.mark.django_db
def test_delete_removes_the_template_and_is_audited(admin):
    c, _ = admin
    assert _post(c, action="delete", key="receipt_email").status_code == 302
    assert not NotificationTemplate.objects.filter(key="receipt_email").exists()
    assert AuditLog.objects.filter(action="Template Deleted").exists()


@pytest.mark.django_db
def test_test_email_goes_to_the_admin_with_sample_values(admin, mailoutbox):
    c, user = admin
    _post(c, action="test", key="receipt_email", name="x", subject="Receipt {reference_id}", body="Paid {amount} {currency} by {student_name}")
    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.to == [user.email] and mail.subject == "[TEST] Receipt IPT-2026-0001"
    assert mail.body == "Paid 15,000.00 TZS by Asha Juma"
    assert AuditLog.objects.filter(action="Template Test Sent").exists()


@pytest.mark.django_db
def test_test_sms_goes_to_the_admins_phone(admin, monkeypatch):
    c, user = admin
    sent = []
    monkeypatch.setattr("apps.core.providers.send_sms", lambda phone, text: sent.append((phone, text)) or {"status": "SENT"})
    _post(c, action="test", key="acceptance_sms", name="x", body="Hi {student_name}")
    assert sent == [("255712000111", "Hi Asha Juma")]


@pytest.mark.django_db
def test_test_sms_needs_a_phone_number(admin, monkeypatch):
    c, user = admin
    user.phone = ""
    user.save(update_fields=["phone"])
    monkeypatch.setattr("apps.core.providers.send_sms", lambda *a: pytest.fail("must not send"))
    response = _post(c, action="test", key="acceptance_sms", name="x", body="Hi")
    assert "no phone number" in response.content.decode()


@pytest.mark.django_db
def test_test_sending_is_rate_limited(admin, mailoutbox):
    c, _ = admin
    for _ in range(7):
        _post(c, action="test", key="receipt_email", name="x", subject="s", body="b")
    assert len(mailoutbox) == 5


@pytest.mark.django_db
def test_an_invalid_template_is_never_test_sent(admin, mailoutbox):
    c, _ = admin
    response = _post(c, action="test", key="receipt_email", name="x", subject="s", body="Hello {nope}")
    assert response.status_code == 200 and not mailoutbox
