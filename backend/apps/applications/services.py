from django.utils.translation import gettext
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.applications.models import Application, ApplicationStatus, PAYMENT_DEADLINE_HOURS


class SlotFullError(Exception):
    pass


def eligibility_blocker(student_profile, slot) -> str:
    """Why this student does not meet the slot's stated requirements, or ``""`` when they do.

    A slot's education level is the *minimum* qualification, so a higher level is fine. The academic
    year only matters when the student is at the slot's level (the Higher Diploma case). An unknown
    value on the student's profile is never held against them.
    """
    from apps.core.education import education_level_label

    required, level = slot.education_level, student_profile.education_level
    if required and level and level < required:
        return gettext(
            "This slot requires at least a %(qualification)s qualification (Level %(required)s). Your profile says Level %(level)s."
        ) % {"qualification": education_level_label(required), "required": required, "level": level}
    year, student_year = slot.academic_year, student_profile.academic_year
    if year and student_year and (not level or level == required) and student_year != year:
        return gettext("This slot is for %(year)s students. Your profile says a different year.") % {"year": slot.get_academic_year_display()}
    return ""


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
            .select_related("organization")
            .first()
        )
        if locked is None:
            raise SlotFullError(gettext("Slot no longer exists."))
        holds_seat = Application.objects.filter(
            student=student_profile,
            slot=locked,
            status__in=[ApplicationStatus.PENDING, ApplicationStatus.PAID],
        ).exists()
        if not holds_seat:
            if locked.deadline_passed:
                raise SlotFullError(gettext("The application deadline for this slot has passed."))
            if locked.organization.is_suspended:
                raise SlotFullError(gettext("This organization is not accepting applications right now."))
            if locked.status in (SlotStatus.PAUSED, SlotStatus.CLOSED):
                raise SlotFullError(gettext("This slot is not accepting applications right now."))
            blocker = eligibility_blocker(student_profile, locked)
            if blocker:
                raise SlotFullError(blocker)
        if locked.is_full:
            raise SlotFullError(gettext("This slot is full."))

        application, _ =Application.objects.get_or_create(
            student=student_profile, slot=locked
        )
        if application.status == ApplicationStatus.PAID:
            raise SlotFullError(gettext("You have already applied to this slot."))
        if application.status == ApplicationStatus.UNPAID:
            # Re-book: the previous reservation expired unpaid, restart it.
            if locked.is_full:
                raise SlotFullError(gettext("This slot is full."))
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


def get_organization_visible_applications(slot):
    """Organization-visible applicants: only those with a verified payment."""
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


class AcceptanceError(Exception):
    """The application cannot be accepted right now (the message is safe to show the user)."""


def acceptance_blocker(application):
    """Why this application cannot be accepted yet, or ``""`` when it can.

    There is no rejecting a paid application: the only decision an organization makes is to accept.
    """
    if application.status != ApplicationStatus.PAID:
        return gettext("This application has no verified payment.")
    if application.is_accepted:
        return gettext("This application is already accepted.")
    if application.is_cancellable:
        return gettext("The student can still cancel for 24 hours after paying. You can accept after that.")
    return ""


def accept_and_notify(application, *, company_message="", request=None, actor=None) -> Application:
    """Accept a paid application and tell the student: in-app notification, SMS and email.

    The one place every accept path (applicants page, dashboard, bulk accept) goes through.
    Raises AcceptanceError when the application cannot be accepted.
    """
    from apps.core.audit import audit_event
    from apps.notifications.inapp import NotificationType, notify

    from .tasks import acceptance_email, acceptance_sms

    blocker = acceptance_blocker(application)
    if blocker:
        raise AcceptanceError(blocker)
    company_message = (company_message or "").strip()[:500]
    actor = actor or getattr(request, "user", None)
    organization = application.slot.organization
    accept_application(application, company_message=company_message)
    audit_event(
        request,
        action="Application Accepted",
        module="Applications",
        actor=actor,
        organization=organization,
        target=application,
        target_label=f"{application.student.user.email} -> {application.slot.title}",
        description="Accepted an application and sent confirmation",
    )
    notify(
        application.student.user,
        NotificationType.ACCEPTANCE,
        "You have been accepted!",
        f"{organization.name} accepted your application for '{application.slot.title}'. "
        "Your acceptance letter is ready to download.",
        link=f"/student/applications/{application.id}/next-steps/",
        actor=actor,
    )
    acceptance_sms.delay(application.id, company_message, actor_id=getattr(actor, "id", None))
    acceptance_email.delay(application.id, company_message)
    return application


@transaction.atomic
def finalize_verified_payment(payment, *, gateway_txn_id="", payload=None, actor=None):
    """Verify a payment and mark the application PAID (Django-side flow).

    - marks the Payment PAID
    - transitions the Application to PAID (NOT accepted yet — the organization
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
            f"{application.slot.organization.name} has been submitted. "
            f"Payment verified ({payment.reference_id}). The organization will review "
            f"your application letter."
        ),
        link="/student/applications/",
    )
    # Tell everyone on the organization's team who may review applicants.
    from apps.companies.permissions import APPLICANTS_VIEW
    from apps.notifications.inapp import notify_many, organization_recipients

    notify_many(
        organization_recipients(application.slot.organization, APPLICANTS_VIEW),
        NotificationType.APPLICATION,
        "New Applicant",
        f"New paid applicant for '{application.slot.title}'.",
        link=f"/organization/slots/{application.slot_id}/applicants/",
        actor=actor,
    )

    receipt_pdf.delay(payment.id)
    receipt_email.delay(payment.id)
    submitted_sms.delay(application.id)
    fanout.delay([notification.id])
    refresh_status.delay(application.slot_id)
    return application