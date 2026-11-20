from celery import shared_task

from apps.notifications.services import (
    OutboundRateLimited,
    create_message,
    render_template,
    throttled_dispatch,
)


@shared_task(name="applications.expire_unpaid")
def expire_unpaid():
    """Mark PENDING applications past their payment deadline as UNPAID.

    Fails their still-pending payments and returns the released slot seats
    to available via slot refresh.
    """
    from django.utils import timezone

    from apps.applications.models import Application, ApplicationStatus
    from apps.payments.models import Payment, PaymentStatus
    from apps.slots.tasks import refresh_status

    now = timezone.now()
    expired = (
        Application.objects.filter(
            status=ApplicationStatus.PENDING,
            payment_deadline__lt=now,
        )
        .values_list("id", "slot_id")
    )
    entries = list(expired)
    slot_ids = {slot_id for _, slot_id in entries}
    app_ids = [app_id for app_id, _ in entries]
    Application.objects.filter(
        id__in=app_ids,
        status=ApplicationStatus.PENDING,
    ).update(status=ApplicationStatus.UNPAID, updated_at=now)
    Payment.objects.filter(
        application_id__in=app_ids,
        status=PaymentStatus.PENDING,
    ).update(status=PaymentStatus.FAILED, updated_at=now)
    for slot_id in slot_ids:
        refresh_status.delay(slot_id)
    return {"expired": len(entries), "slots": len(slot_ids)}


EXPIRY_WARNING_MINUTES = 60


@shared_task(name="applications.remind_expiring")
def remind_expiring():
    """In-app reminder when an unpaid seat will be released within the hour.

    One reminder per reservation: a notification for the same payment page that is newer than the
    reservation itself means the student was already told.
    """
    from datetime import timedelta

    from django.utils import timezone

    from apps.applications.models import PAYMENT_DEADLINE_HOURS, Application, ApplicationStatus
    from apps.notifications.inapp import Notification, NotificationType, notify

    now = timezone.now()
    expiring = Application.objects.filter(
        status=ApplicationStatus.PENDING,
        payment_deadline__gt=now,
        payment_deadline__lte=now + timedelta(minutes=EXPIRY_WARNING_MINUTES),
        payment__is_paid=False,
    ).select_related("student__user", "slot__organization", "payment")
    reminded = 0
    for app in expiring:
        link = f"/student/payments/{app.payment.id}/"
        reserved_at = app.payment_deadline - timedelta(hours=PAYMENT_DEADLINE_HOURS)
        if Notification.objects.filter(
            user=app.student.user, type=NotificationType.PAYMENT, link=link, created_at__gte=reserved_at
        ).exists():
            continue
        minutes = max(1, int((app.payment_deadline - now).total_seconds() // 60))
        notify(
            app.student.user,
            NotificationType.PAYMENT,
            "Your seat is about to be released",
            f"Pay the application fee for '{app.slot.title}' at {app.slot.organization.name} within "
            f"{minutes} minutes, or your seat goes back to other students.",
            link=link,
        )
        reminded += 1
    return {"reminded": reminded}


@shared_task(
    name="applications.submitted_sms",
    autoretry_for=(OutboundRateLimited,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def submitted_sms(application_id):
    """Type A notification — SMS confirming the application was sent.

    Includes Application ID, Organization Name, and selected Slot.
    """
    from apps.applications.models import Application
    from apps.notifications.models import MessageChannel

    app = (
        Application.objects.select_related(
            "student__user", "slot__organization", "payment"
        )
        .get(id=application_id)
    )
    subject, body = render_template(
        "payment_received_sms",
        MessageChannel.SMS,
        {
            "app_id": app.id,
            "organization_name": app.slot.organization.name,
            "slot_title": app.slot.title,
            "student_name": app.student.user.full_name,
        },
        fallback_body=(
            f"Application Sent! App ID: #{app.id} | Organization: {app.slot.organization.name} | "
            f"Slot: {app.slot.title}. Track it on the IPT Marketplace."
        ),
    )
    message = create_message(
        user=app.student.user,
        application=app,
        channel=MessageChannel.SMS,
        subject=subject,
        body=body,
    )
    throttled_dispatch(message)
    return {"message_id": message.id}


@shared_task(
    name="applications.acceptance_sms",
    autoretry_for=(OutboundRateLimited,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def acceptance_sms(application_id, company_message="", actor_id=None):
    """Type B notification — organization-triggered SMS telling the student to
    check their email for the acceptance letter / further instructions.
    """
    from apps.applications.models import Application
    from apps.notifications.models import MessageChannel

    app = Application.objects.select_related("student__user", "slot__organization").get(
        id=application_id
    )
    note = f"\n{company_message}" if company_message else ""
    subject, body = render_template(
        "acceptance_sms",
        MessageChannel.SMS,
        {
            "slot_title": app.slot.title,
            "organization_name": app.slot.organization.name,
            "student_name": app.student.user.full_name,
        },
        fallback_body=(
            f"Congratulations! You have been accepted for {app.slot.title} at "
            f"{app.slot.organization.name}. Check your email for the acceptance letter or "
            f"further instructions.{note}"
        ),
    )
    if note:
        body = f"{body}{note}"
    message = create_message(
        user=app.student.user,
        application=app,
        channel=MessageChannel.SMS,
        subject=subject,
        body=body,
    )
    throttled_dispatch(message)
    return {"message_id": message.id}


@shared_task(
    name="applications.acceptance_email",
    autoretry_for=(OutboundRateLimited,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def acceptance_email(application_id, company_message="", letter_id=None, replacement=False):
    """Type B notification: organization-triggered email confirming acceptance, with the organization's own
    letter (already stamped with its verification QR and locked) attached."""
    import logging

    from apps.applications import emails, letter_services
    from apps.applications.models import AcceptanceLetter, Application
    from apps.notifications.models import MessageChannel

    app = Application.objects.select_related(
        "student__user", "slot__organization", "slot__district__region"
    ).get(id=application_id)
    letters = AcceptanceLetter.objects.filter(application=app)
    letter = letters.filter(id=letter_id).first() if letter_id else letters.filter(is_current=True).first()
    org_name = app.slot.organization.name
    note = f"\n\nNote from {org_name}:\n{company_message}" if company_message else ""
    student_name = app.student.user.full_name or app.student.user.email
    subject, body = render_template(
        "acceptance_email",
        MessageChannel.EMAIL,
        {
            "student_name": student_name,
            "organization_name": org_name,
            "slot_title": app.slot.title,
            "app_id": app.id,
            "company_message": company_message,
            "company_note": note,
        },
        fallback_subject=f"Congratulations! You have been accepted — {app.slot.title}",
        fallback_body=(
            f"Dear {student_name},\n\n"
            f"Congratulations! Your application (ID: #{app.id}) for '{app.slot.title}' "
            f"at {org_name} has been accepted.\n\n"
            f"Your official acceptance letter is attached to this email. Contact {org_name} if "
            f"you have any questions.{note}\n\n"
            f"Best regards,\n{org_name}"
        ),
    )
    if replacement:
        subject = f"Updated acceptance letter — {app.slot.title}"
        body = (
            f"Dear {student_name},\n\n{org_name} has sent you a corrected acceptance letter "
            f"for '{app.slot.title}'. It replaces the earlier letter; please use this one from now on."
        )
    html, attachments = None, None
    if letter is not None:
        body = (
            f"{body.rstrip()}\n\nVerify this letter:\n{letter_services.verification_url(letter.id)}"
            f"\n\nOpen the platform:\n{emails.app_url(app)}"
        )
        data = letter_services.read_verified(letter)
        if data is None:  # missing or altered on disk: never send a file that does not match its fingerprint
            logging.getLogger("security").error("Acceptance letter %s failed its integrity check; not attached", letter.id)
        else:
            attachments = [{
                "filename": letter_services.letter_filename(app),
                "content": data,
                "mimetype": "application/pdf",
            }]
        try:
            html = emails.build_acceptance_email(app, letter, subject, note=company_message, replacement=replacement)
        except Exception:  # noqa: BLE001 - the email must still go out in the standard branded layout
            logging.getLogger(__name__).warning("Acceptance email layout failed; sending the standard layout", exc_info=True)
    message = create_message(
        user=app.student.user,
        application=app,
        channel=MessageChannel.EMAIL,
        subject=subject,
        body=body,
    )
    throttled_dispatch(message, attachments=attachments, html_content=html)
    return {"message_id": message.id}
