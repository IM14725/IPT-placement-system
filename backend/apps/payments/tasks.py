from celery import shared_task

from apps.notifications.services import (
    OutboundRateLimited,
    create_message,
    dispatch_message,
    render_template,
    throttled_dispatch,
)


@shared_task(
    name="payments.receipt_email",
    autoretry_for=(OutboundRateLimited,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def receipt_email(payment_id):
    """Type A notification — email payment receipt with the PDF attached."""
    from apps.notifications.models import MessageChannel
    from apps.payments.models import Payment
    from apps.payments.services import build_payment_receipt

    payment = Payment.objects.select_related(
        "student__user", "application__slot__company"
    ).get(id=payment_id)
    pdf_path = build_payment_receipt(payment)
    app = payment.application

    subject, body = render_template(
        "receipt_email",
        MessageChannel.EMAIL,
        {
            "student_name": app.student.user.get_full_name() or app.student.user.email,
            "company_name": app.slot.company.name,
            "slot_title": app.slot.title,
            "amount": f"{payment.amount:,.2f}",
            "currency": payment.currency,
            "reference_id": payment.reference_id,
            "paid_at": f"{payment.paid_at:%Y-%m-%d %H:%M}",
        },
        fallback_subject=f"Payment Receipt - {payment.reference_id}",
        fallback_body=(
            f"Dear {app.student.user.get_full_name() or app.student.user.email},\n\n"
            f"Thank you for your application to {app.slot.company.name} "
            f"({app.slot.title}). Your application fee of {payment.amount:,.2f} {payment.currency} "
            f"has been received.\n\n"
            f"Reference: {payment.reference_id}\n"
            f"Paid on: {payment.paid_at:%Y-%m-%d %H:%M}\n\n"
            f"Your application has been submitted and accepted. Please see the attached receipt.\n\n"
            f"IPT Marketplace"
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
    return {"message_id": message.id, "pdf": pdf_path}