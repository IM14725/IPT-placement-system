from celery import shared_task


@shared_task(name="ledger.aggregate")
def aggregate():
    """Aggregate platform metrics (financial ledger snapshot)."""
    from django.db.models import Count, Sum

    from apps.applications.models import Application, ApplicationStatus
    from apps.companies.models import CompanyProfile
    from apps.payments.models import Payment, PaymentStatus
    from apps.students.models import StudentProfile, VerificationStatus

    totals = Payment.objects.filter(status=PaymentStatus.PAID).aggregate(
        total_fees=Sum("amount"), paid_count=Count("id")
    )
    snapshot = {
        "total_fees": float(totals["total_fees"] or 0),
        "paid_transactions": totals["paid_count"] or 0,
        "verified_students": StudentProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).count(),
        "approved_companies": CompanyProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).count(),
        "active_placements": Application.objects.filter(
            status=ApplicationStatus.PAID
        ).count(),
    }
    from apps.core.models import LedgerSnapshot

    LedgerSnapshot.objects.create(data=snapshot)
    return snapshot