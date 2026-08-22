import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import AuditLog, PlatformSetting


def health(request):
    from django.db import connection

    db_ok = True
    try:
        connection.ensure_connection()
    except Exception:  # noqa: BLE001
        db_ok = False
    return JsonResponse({"status": "ok", "database": "ok" if db_ok else "error"})


def django_admin_redirect(request):
    """Disable the built-in Django admin site.

    Any attempt to reach /admin/ is sent to the styled platform console
    instead of Django's admin.
    """
    if request.user.is_authenticated and (request.user.is_staff or request.user.is_platform_admin):
        return redirect("platform-verifications")
    if request.user.is_authenticated:
        return redirect("home")
    return redirect("login")


def _require_admin(request):
    return request.user.is_authenticated and (
        request.user.is_staff or request.user.is_platform_admin
    )


def _write_audit(request, action, module, description, *, actor=None, is_system=False):
    actor = actor or request.user
    AuditLog.objects.create(
        actor=actor if actor.is_authenticated else None,
        actor_label=actor.get_full_name() or (actor.email if actor.is_authenticated else "System"),
        action=action,
        module=module,
        description=description,
        ip_address=_client_ip(request),
        is_system=is_system,
    )


def _client_ip(request):
    return request.META.get("REMOTE_ADDR")


def _get_setting(key, default=None):
    try:
        return PlatformSetting.objects.get(key=key).get_value()
    except PlatformSetting.DoesNotExist:
        return default


@login_required
def ledger(request):
    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    from apps.payments.models import Payment, PaymentStatus
    from apps.core.cache import cache_get_or_set
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    payments = (
        Payment.objects.filter(status=PaymentStatus.PAID)
        .select_related("student__user", "application__slot__company")
        .order_by("-paid_at")
    )
    totals = cache_get_or_set(
        "admin:ledger-totals",
        30,
        producer=lambda: Payment.objects.filter(status=PaymentStatus.PAID).aggregate(
            total_fees=Sum("amount"), paid_count=Count("id")
        ),
    )
    page_obj = paginate(payments, request.GET.get("page"), page_size=50)
    return render(
        request,
        "core/ledger.html",
        {
            "payments": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "total_fees": totals["total_fees"] or 0,
            "paid_count": totals["paid_count"] or 0,
        },
    )


@login_required
def metrics(request):
    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    from apps.core.cache import cache_get_or_set

    data = cache_get_or_set("admin:metrics", 60, producer=_compute_metrics)
    return render(request, "core/metrics.html", {"data": data})


def _compute_metrics():
    from datetime import timedelta

    from apps.accounts.models import User
    from apps.applications.models import Application, ApplicationStatus
    from apps.companies.models import CompanyProfile
    from apps.payments.models import Payment, PaymentStatus
    from apps.slots.models import Slot, SlotStatus
    from apps.students.models import StudentProfile, VerificationStatus
    from django.db.models.functions import TruncDate

    from apps.core.cache import daily_series

    days = 30
    start = timezone.localdate() - timedelta(days=days - 1)

    def _zero_filled(by_day):
        return [
            {
                "date": (start + timedelta(days=i)).isoformat(),
                "value": by_day.get((start + timedelta(days=i)).isoformat(), 0),
            }
            for i in range(days)
        ]

    # Revenue trend: real paid-payment history aggregated per day.
    revenue_by_day = {
        d.isoformat(): float(total)
        for d, total in Payment.objects.filter(
            status=PaymentStatus.PAID, paid_at__isnull=False
        )
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(total=Sum("amount"))
        .values_list("day", "total")
    }

    # Login trend: distinct users who last logged in each day (Django updates
    # ``last_login`` on every successful ``login()``).
    login_by_day = {
        d.isoformat(): int(n)
        for d, n in User.objects.exclude(last_login=None)
        .annotate(day=TruncDate("last_login"))
        .values("day")
        .annotate(n=Count("id"))
        .values_list("day", "n")
    }

    # Slot-search trend: Redis per-day counter (started now; 0 for earlier days).
    search_series = daily_series("searches", days=days)

    return {
        "users": User.objects.count(),
        "students": StudentProfile.objects.count(),
        "students_verified": StudentProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).count(),
        "companies": CompanyProfile.objects.count(),
        "companies_approved": CompanyProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).count(),
        "slots": Slot.objects.count(),
        "slots_open": Slot.objects.filter(status=SlotStatus.OPEN).count(),
        "slots_full": Slot.objects.filter(status=SlotStatus.FULL).count(),
        "applications": Application.objects.count(),
        "applications_paid": Application.objects.filter(
            status=ApplicationStatus.PAID
        ).count(),
        "applications_accepted": Application.objects.filter(
            is_accepted=True
        ).count(),
        "payments": Payment.objects.filter(status=PaymentStatus.PAID).count(),
        "total_fees": Payment.objects.filter(status=PaymentStatus.PAID).aggregate(
            total=Sum("amount")
        )["total"]
        or 0,
        "revenue_series": _zero_filled(revenue_by_day),
        "login_series": _zero_filled(login_by_day),
        "search_series": search_series,
    }


# ---------------------------------------------------------------------------
# Phase 5 — Admin Console
# ---------------------------------------------------------------------------

def _verification_context():
    from apps.companies.models import CompanyProfile
    from apps.students.models import StudentProfile, VerificationStatus

    students = (
        StudentProfile.objects.filter(verification_status=VerificationStatus.PENDING)
        .select_related("user", "region")
        .order_by("-created_at")
    )
    companies = (
        CompanyProfile.objects.filter(verification_status=VerificationStatus.PENDING)
        .select_related("user", "region")
        .order_by("-created_at")
    )
    total_pending = students.count() + companies.count()
    today = timezone.localdate()
    verified_today = (
        StudentProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED, reviewed_at__date=today
        ).count()
        + CompanyProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED, reviewed_at__date=today
        ).count()
    )
    return {
        "students": students,
        "companies": companies,
        "total_pending": total_pending,
        "verified_today": verified_today,
        "student_pending": students.count(),
        "company_pending": companies.count(),
    }


@login_required
def verification_queue(request):
    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    return render(request, "core/verification_queue.html", _verification_context())


def _required_docs(profile_kind):
    from apps.documents.models import Document

    if profile_kind == "student":
        # Student ID card is captured on the profile (id_card_photo + student_id);
        # the results matrix is optional — no documents are mandatory for approval.
        return []
    return [
        Document.DocType.BRELA_CERT,
        Document.DocType.TIN_CERT,
        Document.DocType.BUSINESS_LICENSE,
    ]


def _missing_docs(owner_user, profile_kind):
    from apps.documents.models import Document

    have = set(
        Document.objects.filter(owner=owner_user, doc_type__in=_required_docs(profile_kind))
        .values_list("doc_type", flat=True)
    )
    return [d for d in _required_docs(profile_kind) if d not in have]


@login_required
def student_verification(request, pk):
    from apps.documents.models import Document
    from apps.students.models import StudentProfile, VerificationStatus

    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    student = get_object_or_404(StudentProfile, pk=pk)
    docs = Document.objects.filter(owner=student.user).select_related("owner")
    if request.method == "POST":
        decision = request.POST.get("decision")
        reason = request.POST.get("reason", "").strip()
        if decision == "approve":
            missing = _missing_docs(student.user, "student")
            if missing:
                messages.error(request, "Missing required documents — cannot approve.")
            else:
                student.verification_status = VerificationStatus.APPROVED
                student.rejection_reason = ""
                student.reviewed_by = request.user
                student.reviewed_at = timezone.now()
                student.save(
                    update_fields=[
                        "verification_status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                _write_audit(request, "Student Approved", "Verification",
                             f"Approved student {student.user.email} ({student.university}).")
                messages.success(request, "Student profile approved.")
                return redirect("platform-verifications")
        elif decision == "reject":
            if not reason:
                messages.error(request, "A rejection reason is required.")
            else:
                student.verification_status = VerificationStatus.REJECTED
                student.rejection_reason = reason
                student.reviewed_by = request.user
                student.reviewed_at = timezone.now()
                student.save(
                    update_fields=[
                        "verification_status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                _write_audit(request, "Student Rejected", "Verification",
                             f"Rejected student {student.user.email}: {reason}")
                messages.success(request, "Student profile rejected.")
                return redirect("platform-verifications")
    return render(
        request,
        "core/student_verification.html",
        {"student": student, "docs": docs, "missing_docs": _missing_docs(student.user, "student")},
    )


@login_required
def company_verification(request, pk):
    from apps.companies.models import CompanyProfile
    from apps.documents.models import Document
    from apps.students.models import VerificationStatus

    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    company = get_object_or_404(CompanyProfile, pk=pk)
    docs = Document.objects.filter(owner=company.user).select_related("owner")
    if request.method == "POST":
        decision = request.POST.get("decision")
        reason = request.POST.get("reason", "").strip()
        if decision == "approve":
            missing = _missing_docs(company.user, "company")
            if missing:
                messages.error(request, "Missing required documents — cannot approve.")
            else:
                company.verification_status = VerificationStatus.APPROVED
                company.rejection_reason = ""
                company.reviewed_by = request.user
                company.reviewed_at = timezone.now()
                company.save(
                    update_fields=[
                        "verification_status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                _write_audit(request, "Company Approved", "Verification",
                             f"Approved company {company.name} ({company.industry}).")
                messages.success(request, "Company profile approved.")
                return redirect("platform-verifications")
        elif decision == "reject":
            if not reason:
                messages.error(request, "A rejection reason is required.")
            else:
                company.verification_status = VerificationStatus.REJECTED
                company.rejection_reason = reason
                company.reviewed_by = request.user
                company.reviewed_at = timezone.now()
                company.save(
                    update_fields=[
                        "verification_status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                _write_audit(request, "Company Rejected", "Verification",
                             f"Rejected company {company.name}: {reason}")
                messages.success(request, "Company profile rejected.")
                return redirect("platform-verifications")
    return render(
        request,
        "core/company_verification.html",
        {"company": company, "docs": docs, "missing_docs": _missing_docs(company.user, "company")},
    )


@login_required
def notification_templates(request):
    from apps.notifications.models import NotificationTemplate

    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    templates = NotificationTemplate.objects.all().order_by("key")
    selected = None
    if request.method == "POST":
        key = request.POST.get("key", "").strip()
        name = request.POST.get("name", "").strip()
        channel = request.POST.get("channel")
        subject = request.POST.get("subject", "")
        body = request.POST.get("body", "")
        description = request.POST.get("description", "")
        trigger_label = request.POST.get("trigger_label", "")
        is_active = request.POST.get("is_active") == "on"
        if key and name:
            template, _ = NotificationTemplate.objects.update_or_create(
                key=key,
                defaults={
                    "name": name,
                    "channel": channel,
                    "subject": subject,
                    "body": body,
                    "description": description,
                    "trigger_label": trigger_label,
                    "is_active": is_active,
                },
            )
            _write_audit(request, "Template Saved", "Notification Templates",
                         f"Saved notification template '{template.name}' ({template.key}).")
            messages.success(request, "Template saved.")
            return redirect(f"{request.path}?key={template.key}")
        messages.error(request, "Key and name are required.")
    key = request.GET.get("key")
    if key:
        selected = templates.filter(key=key).first() or templates.first()
    elif templates.exists():
        selected = templates.first()
    return render(
        request,
        "core/notification_templates.html",
        {"templates": templates, "selected": selected},
    )


@login_required
def audit_logs(request):
    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    q = request.GET.get("q", "").strip()
    module = request.GET.get("module", "")
    logs = AuditLog.objects.all().select_related("actor")
    if q:
        from django.db.models import Q

        logs = logs.filter(
            Q(actor_label__icontains=q)
            | Q(action__icontains=q)
            | Q(description__icontains=q)
            | Q(module__icontains=q)
        )
    if module:
        logs = logs.filter(module=module)
    logs = logs.order_by("-created_at")
    modules = list(AuditLog.objects.values_list("module", flat=True).distinct().order_by("module"))
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    page_obj = paginate(logs, request.GET.get("page"), page_size=50)
    return render(
        request,
        "core/audit_logs.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "modules": modules,
            "q": q,
            "module": module,
            "total": AuditLog.objects.count(),
        },
    )


@login_required
def role_management(request):
    from apps.accounts.models import AdminRole

    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    roles = AdminRole.objects.all().order_by("name")
    role_id = request.GET.get("role") or request.POST.get("role_id")
    active_role = roles.filter(pk=role_id).first() if role_id else (roles.first() if roles.exists() else None)
    if request.method == "POST":
        active_role = get_object_or_404(AdminRole, pk=request.POST.get("role_id"))
        permissions = {}
        for module in ("user_management", "slot_verification", "financial_ledger", "system_metrics"):
            permissions[module] = {
                cap: request.POST.get(f"perm_{module}_{cap}") == "on"
                for cap in ("view", "create", "edit", "delete")
            }
        active_role.permissions = permissions
        active_role.privileges = {
            "bypass_approval": request.POST.get("priv_bypass_approval") == "on",
            "impersonate": request.POST.get("priv_impersonate") == "on",
        }
        active_role.save()
        _write_audit(request, "Role Updated", "Role Management",
                     f"Updated permissions for role '{active_role.name}'.")
        messages.success(request, "Role permissions updated.")
        return redirect(f"{request.path}?role={active_role.pk}")

    modules = [
        ("user_management", "User Management", "Control over student and corporate accounts"),
        ("slot_verification", "Slot Verification", "Approve or reject posted opportunities"),
        ("financial_ledger", "Financial Ledger", "View transactions and manage fee structures"),
        ("system_metrics", "System Metrics", "Access to analytics and reporting dashboards"),
    ]
    caps = ("view", "create", "edit", "delete")
    active_perms = active_role.permissions if active_role else {}
    matrix = []
    for key, label, desc in modules:
        row = {"key": key, "label": label, "desc": desc, "caps": []}
        for cap in caps:
            row["caps"].append((cap, bool(active_perms.get(key, {}).get(cap))))
        matrix.append(row)
    return render(
        request,
        "core/role_management.html",
        {
            "roles": roles,
            "active_role": active_role,
            "matrix": matrix,
            "caps": caps,
        },
    )


@login_required
def admin_settings(request):
    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)
    if request.method == "POST":
        updates = {
            "app_fee_amount": (request.POST.get("app_fee_amount"), "number"),
            "max_applications_per_student": (request.POST.get("max_applications_per_student"), "number"),
            "default_slot_limit": (request.POST.get("default_slot_limit"), "number"),
            "sms_enabled": (request.POST.get("sms_enabled") == "on", "bool"),
            "sms_api_endpoint": (request.POST.get("sms_api_endpoint"), "text"),
            "sms_api_key": (request.POST.get("sms_api_key"), "text"),
            "maintenance_mode": (request.POST.get("maintenance_mode") == "on", "bool"),
            "student_req_student_id": (request.POST.get("student_req_student_id") == "on", "bool"),
            "student_req_transcript": (request.POST.get("student_req_transcript") == "on", "bool"),
            "student_req_cv": (request.POST.get("student_req_cv") == "on", "bool"),
        }
        for key, (value, vtype) in updates.items():
            setting, _ = PlatformSetting.objects.get_or_create(
                key=key,
                defaults={
                    "label": key.replace("_", " ").title(),
                    "value_type": vtype,
                    "value_text": "" if vtype != "text" else "",
                    "value_number": None if vtype != "number" else (float(value) if value not in (None, "") else 0),
                    "value_bool": False,
                },
            )
            if vtype == "number":
                setting.value_number = float(value) if value not in (None, "") else 0
            elif vtype == "bool":
                setting.value_bool = bool(value)
            else:
                setting.value_text = value or ""
            setting.value_type = vtype
            setting.save()
        _write_audit(request, "Settings Updated", "Platform Configuration",
                     "Platform settings were updated by an administrator.")
        messages.success(request, "Settings saved.")
        return redirect("platform-settings")
    settings = {s.key: s for s in PlatformSetting.objects.all()}
    return render(request, "core/admin_settings.html", {"settings": settings})


@login_required
def directory(request):
    """Admin directory of all students and companies with filters."""
    from apps.companies.models import CompanyProfile
    from apps.locations.models import District, Region, Ward
    from apps.students.models import StudentProfile, VerificationStatus

    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)

    tab = request.GET.get("tab", "students")
    region_id = request.GET.get("region") or None
    district_id = request.GET.get("district") or None
    ward_id = request.GET.get("ward") or None
    university = (request.GET.get("university") or "").strip()
    course = (request.GET.get("course") or "").strip()
    year = request.GET.get("year") or None
    education_level = request.GET.get("education_level") or None
    status = request.GET.get("status") or None

    if tab == "companies":
        qs = CompanyProfile.objects.select_related("user", "region", "district", "ward").order_by("-created_at")
        if region_id:
            qs = qs.filter(region_id=region_id)
        if district_id:
            qs = qs.filter(district_id=district_id)
        if ward_id:
            qs = qs.filter(ward_id=ward_id)
        if status:
            qs = qs.filter(verification_status=status)
        rows = qs
    else:
        tab = "students"
        qs = StudentProfile.objects.select_related("user", "region", "district", "ward").order_by("-created_at")
        if region_id:
            qs = qs.filter(region_id=region_id)
        if district_id:
            qs = qs.filter(district_id=district_id)
        if ward_id:
            qs = qs.filter(ward_id=ward_id)
        if university:
            qs = qs.filter(university__icontains=university)
        if course:
            qs = qs.filter(course__icontains=course)
        if year:
            qs = qs.filter(current_year=year)
        if education_level:
            qs = qs.filter(education_level=education_level)
        if status:
            qs = qs.filter(verification_status=status)
        rows = qs

    regions = list(Region.objects.order_by("name").values("id", "name"))
    districts = (
        list(District.objects.filter(region_id=region_id).order_by("name").values("id", "name"))
        if region_id
        else []
    )
    wards = (
        list(Ward.objects.filter(district_id=district_id).order_by("name").values("id", "name"))
        if district_id
        else []
    )
    years = [i for i in range(1, 9)]
    statuses = VerificationStatus.choices
    from apps.core.education import education_level_choices
    from apps.core.cache import get_institutions
    from apps.core.pagination import paginate
    from urllib.parse import urlencode

    institutions = get_institutions()
    page_obj = paginate(rows, request.GET.get("page"), page_size=25)
    return render(
        request,
        "core/directory.html",
        {
            "tab": tab,
            "rows": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
            "regions": regions,
            "districts": districts,
            "wards": wards,
            "regions_json": json.dumps(regions),
            "institutions_json": json.dumps(institutions),
            "years": years,
            "statuses": statuses,
            "education_levels": education_level_choices(),
            "filters": {
                "region": region_id,
                "district": district_id,
                "ward": ward_id,
                "university": university,
                "course": course,
                "year": year,
                "education_level": education_level or "",
                "status": status,
            },
        },
    )


@login_required
def integrity(request):
    """Enterprise integrity dashboard — verifies the immutable ledger chain."""
    if not _require_admin(request):
        return render(request, "core/forbidden.html", status=403)

    from apps.applications.models import Application
    from apps.core.immutability import verify_chain, verify_record
    from apps.core.models import AuditLog, IntegrityRecord
    from apps.core.signals import (
        _application_fields,
        _audit_fields,
        _document_fields,
        _payment_fields,
    )
    from apps.documents.models import Document
    from apps.payments.models import Payment

    summary = []
    total_issues = 0
    for label, rtype, model, fields_fn in (
        ("Audit Log", "AUDIT", AuditLog, _audit_fields),
        ("Payments", "PAYMENT", Payment, _payment_fields),
        ("Applications", "APPLICATION", Application, _application_fields),
        ("Documents", "DOCUMENT", Document, _document_fields),
    ):
        ledger = list(
            IntegrityRecord.objects.filter(record_type=rtype).order_by("id")
        )
        latest_by_record = {}
        for rec in ledger:
            latest_by_record[rec.record_id] = rec
        issues = []
        for rec in ledger:
            if not verify_record(IntegrityRecord, rec, rec.payload):
                issues.append(
                    f"Ledger #{rec.id}: {rtype}#{rec.record_id} hash mismatch (tampered)"
                )
            if rec is latest_by_record[rec.record_id]:
                obj = model.objects.filter(id=rec.record_id).first()
                if obj is None:
                    issues.append(
                        f"Ledger #{rec.id}: {rtype}#{rec.record_id} no longer exists"
                    )
                elif not verify_record(IntegrityRecord, rec, fields_fn(obj)):
                    issues.append(
                        f"Ledger #{rec.id}: {rtype}#{rec.record_id} diverges from current record"
                    )
        summary.append(
            {"label": label, "rtype": rtype, "ledger_count": len(ledger), "issues": issues}
        )
        total_issues += len(issues)

    chain_issues = verify_chain(IntegrityRecord)
    return render(
        request,
        "core/integrity.html",
        {
            "summary": summary,
            "chain_issues": chain_issues,
            "total_issues": total_issues + len(chain_issues),
            "ledger_total": IntegrityRecord.objects.count(),
        },
    )