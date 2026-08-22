from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.applications.models import Application, ApplicationStatus, PAYMENT_DEADLINE_HOURS


class SlotFullError(Exception):
    pass


def create_application(student_profile, slot) -> Application:
    """Atomically reserve a slot seat before payment.

    Locks the slot row so two concurrent applicants cannot both fill the
    last remaining seat. Raises SlotFullError when full.
    """
    from apps.slots.models import Slot, SlotStatus

    with transaction.atomic():
        locked = (
            Slot.objects.select_for_update()
            .filter(pk=slot.pk)
            .select_related("company")
            .first()
        )
        if locked is None:
            raise SlotFullError("Slot no longer exists.")
        if locked.is_full:
            raise SlotFullError("This slot is full.")

        application, _ = Application.objects.get_or_create(
            student=student_profile, slot=locked
        )
        if application.status == ApplicationStatus.PAID:
            raise SlotFullError("You have already applied to this slot.")
        if application.status == ApplicationStatus.UNPAID:
            # Re-book: the previous reservation expired unpaid, restart it.
            if locked.is_full:
                raise SlotFullError("This slot is full.")
            application.status = ApplicationStatus.PENDING
            application.payment_deadline = timezone.now() + timedelta(
                hours=PAYMENT_DEADLINE_HOURS
            )
            application.save(
                update_fields=["status", "payment_deadline", "updated_at"]
            )
            return application

        if application.payment_deadline is None:
            application.payment_deadline = timezone.now() + timedelta(
                hours=PAYMENT_DEADLINE_HOURS
            )
            application.save(update_fields=["payment_deadline", "updated_at"])

        return application


def get_company_visible_applications(slot):
    """Company-visible applicants: only those with a verified payment."""
    return (
        Application.objects.filter(slot=slot, status=ApplicationStatus.PAID)
        .select_related("student__user", "payment")
    )


def accept_application(application, *, company_message="") -> Application:
    if application.status != ApplicationStatus.PAID:
        raise ValueError("Application has no verified payment.")
    application.is_accepted = True
    application.company_message = company_message
    application.save(update_fields=["is_accepted", "company_message", "updated_at"])
    return application


@transaction.atomic
def finalize_verified_payment(payment, *, gateway_txn_id="", payload=None, actor=None):
    """Verify a payment and mark the application PAID (Django-side flow).

    - marks the Payment PAID
    - transitions the Application to PAID (NOT accepted yet — the company
      confirms after reviewing the application letter)
    - records the Type A notification
    - fires the Celery tasks (receipt email, submitted SMS, fanout, slot refresh)

    Raises SlotFullError if the slot hit capacity at the moment of acceptance.
    """
    from apps.notifications.models import Notification, NotificationType
    from apps.notifications.tasks import fanout
    from apps.documents.tasks import receipt_pdf
    from apps.payments.tasks import receipt_email
    from apps.slots.tasks import refresh_status
    from .tasks import submitted_sms

    payment.mark_paid(gateway_txn_id=gateway_txn_id, payload=payload)
    application = payment.application
    application.status = ApplicationStatus.PAID
    application.save(update_fields=["status", "updated_at"])

    notification = Notification.objects.create(
        user=application.student.user,
        actor=actor,
        type=NotificationType.APPLICATION,
        title="Application Submitted",
        body=(
            f"Your application for '{application.slot.title}' at "
            f"{application.slot.company.name} has been submitted. "
            f"Payment verified ({payment.reference_id}). The company will review "
            f"your application letter."
        ),
    )

    receipt_pdf.delay(payment.id)
    receipt_email.delay(payment.id)
    submitted_sms.delay(application.id)
    fanout.delay([notification.id])
    refresh_status.delay(application.slot_id)
    return application