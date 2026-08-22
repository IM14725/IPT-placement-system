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


@shared_task(
    name="applications.submitted_sms",
    autoretry_for=(OutboundRateLimited,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def submitted_sms(application_id):
    """Type A notification — SMS confirming the application was sent.

    Includes Application ID, Company Name, and selected Slot.
    """
    from apps.applications.models import Application
    from apps.notifications.models import MessageChannel

    app = (
        Application.objects.select_related(
            "student__user", "slot__company", "payment"
        )
        .get(id=application_id)
    )
    subject, body = render_template(
        "payment_received_sms",
        MessageChannel.SMS,
        {
            "app_id": app.id,
            "company_name": app.slot.company.name,
            "slot_title": app.slot.title,
            "student_name": app.student.user.full_name,
        },
        fallback_body=(
            f"Application Sent! App ID: #{app.id} | Company: {app.slot.company.name} | "
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
    """Type B notification — company-triggered SMS telling the student to
    check their email for the acceptance letter / further instructions.
    """
    from apps.applications.models import Application
    from apps.notifications.models import MessageChannel

    app = Application.objects.select_related("student__user", "slot__company").get(
        id=application_id
    )
    note = f"\n{company_message}" if company_message else ""
    subject, body = render_template(
        "acceptance_sms",
        MessageChannel.SMS,
        {
            "slot_title": app.slot.title,
            "company_name": app.slot.company.name,
            "student_name": app.student.user.full_name,
        },
        fallback_body=(
            f"Congratulations! You have been accepted for {app.slot.title} at "
            f"{app.slot.company.name}. Check your email for the acceptance letter or "
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
def acceptance_email(application_id, company_message=""):
    """Type B notification — company-triggered email confirming acceptance and
    asking the student to open their email for the acceptance letter.
    """
    from apps.applications.models import Application
    from apps.notifications.models import MessageChannel

    app = Application.objects.select_related("student__user", "slot__company").get(
        id=application_id
    )
    note = f"\n\nNote from {app.slot.company.name}:\n{company_message}" if company_message else ""
    subject, body = render_template(
        "acceptance_email",
        MessageChannel.EMAIL,
        {
            "student_name": app.student.user.full_name or app.student.user.email,
            "company_name": app.slot.company.name,
            "slot_title": app.slot.title,
            "app_id": app.id,
            "company_message": company_message,
        },
        fallback_subject=f"Congratulations! You have been accepted — {app.slot.title}",
        fallback_body=(
            f"Dear {app.student.user.full_name or app.student.user.email},\n\n"
            f"Congratulations! Your application (ID: #{app.id}) for '{app.slot.title}' "
            f"at {app.slot.company.name} has been accepted.\n\n"
            f"Please open your email and check for the official acceptance letter and "
            f"further instructions from the company. Contact {app.slot.company.name} if "
            f"you have any questions.{note}\n\n"
            f"Best regards,\n{app.slot.company.name}"
        ),
    )
    message = create_message(
        user=app.student.user,
        application=app,
        channel=MessageChannel.EMAIL,
        subject=subject,
        body=body,
    )
    throttled_dispatch(message)
    return {"message_id": message.id}