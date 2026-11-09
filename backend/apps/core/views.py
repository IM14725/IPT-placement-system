import json
import math

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.decorators import hardware_reverify
from apps.core.audit import client_ip
from apps.core.models import AuditLog, PlatformSetting
from apps.core.rate_limit import token_bucket
from apps.core.security import safe_json_dumps


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


# ── Public legal pages ────────────────────────────────────────────────────────

def legal_terms(request):
    """Terms & Conditions page — publicly accessible, no login required."""
    return render(request, "legal/terms.html")


def legal_privacy(request):
    """Privacy Policy page — publicly accessible, no login required."""
    return render(request, "legal/privacy.html")


@login_required
@token_bucket(capacity=5, refill_per_second=1 / 60, scope="pdpa-request", methods=["POST"])
def legal_pdpa_request(request):
    """PDPA rights request form — Features 4 & 5 (Privacy §6)."""
    if request.method == "POST":
        request_type = request.POST.get("request_type", "")
        if request_type in ("portability", "access"):
            # Immediate data export as JSON download
            user = request.user
            data = {
                "id": str(user.id),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "phone": user.phone,
                "role": user.role,
                "created_at": user.created_at.isoformat(),
            }
            response = JsonResponse(data)
            response["Content-Disposition"] = "attachment; filename=ipt-my-data.json"
            return response
        else:
            # Log the request and redirect back
            details = request.POST.get("details", "")[:500]
            AuditLog.objects.create(
                actor=request.user,
                actor_label=request.user.email,
                action="PDPA_REQUEST",
                module="accounts",
                description=f"Request type: {request_type}. Details: {details}",
                ip_address=_client_ip(request),
            )
            messages.success(
                request,
                "Your PDPA rights request has been submitted. "
                "We will respond within 21 days as required by the PDPA.",
            )
            return redirect("legal-pdpa-request")
    return render(request, "legal/pdpa_request.html")


# ─────────────────────────────────────────────────────────────────────────────


def _require_admin(request, module=None, cap=None, super_only=False):
    """Platform console gate.

    Super Admins pass everything. Platform Admins need ``module``/``cap`` granted by
    their AdminRole (``cap`` defaults to view for reads, edit for writes). ``super_only``
    pages (roles, settings, templates) reject Platform Admins outright.
    """
    user = request.user
    if not user.is_authenticated:
        return False
    if user.is_super_admin:
        return True
    if super_only or not user.is_platform_admin:
        return False
    if module is None:
        return False
    if cap is None:
        cap = "view" if request.method in ("GET", "HEAD", "OPTIONS") else "edit"
    return user.has_admin_permission(module, cap)


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
    return client_ip(request)


def _get_setting(key, default=None):
    try:
        return PlatformSetting.objects.get(key=key).get_value()
    except PlatformSetting.DoesNotExist:
        return default


@login_required
def ledger(request):
    if not _require_admin(request, module="financial_ledger"):
        return render(request, "core/forbidden.html", status=403)
    import datetime
    from apps.payments.models import Payment, PaymentStatus
    from apps.applications.models import Application, ApplicationStatus
    from apps.core.cache import cache_get_or_set
    from apps.core.pagination import paginate
    from urllib.parse import urlencode
    from django.db.models import Q

    payments = Payment.objects.select_related("student__user", "application__slot__organization")

    q = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status")
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    if q:
        payments = payments.filter(
            Q(reference_id__icontains=q)
            | Q(student__user__email__icontains=q)
            | Q(student__user__first_name__icontains=q)
            | Q(student__user__last_name__icontains=q)
            | Q(application__slot__organization__name__icontains=q)
            | Q(amount__icontains=q)
        )

    if status_filter == "paid":
        payments = payments.filter(status=PaymentStatus.PAID, application__status=ApplicationStatus.PAID)
    elif status_filter == "unpaid":
        payments = payments.filter(
            Q(status__in=[PaymentStatus.PENDING, PaymentStatus.FAILED]) | 
            Q(application__status__in=[ApplicationStatus.UNPAID, ApplicationStatus.CANCELLED])
        )

    if start_date:
        try:
            start_date_parsed = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            payments = payments.filter(created_at__gte=start_date_parsed)
        except ValueError:
            pass
            
    if end_date:
        try:
            end_date_parsed = datetime.datetime.strptime(end_date, "%Y-%m-%d").date()
            payments = payments.filter(created_at__lt=end_date_parsed + datetime.timedelta(days=1))
        except ValueError:
            pass

    payments = payments.order_by("-created_at")

    totals = cache_get_or_set(
        "admin:ledger-totals",
        30,
        producer=lambda: Payment.objects.filter(status=PaymentStatus.PAID).aggregate(
            total_fees=Sum("amount"), paid_count=Count("id")
        ),
    )
    
    unpaid_cancelled_count = cache_get_or_set(
        "admin:ledger-unpaid-cancelled-count",
        30,
        producer=lambda: Application.objects.filter(status__in=[ApplicationStatus.UNPAID, ApplicationStatus.CANCELLED]).count()
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
            "unpaid_cancelled_count": unpaid_cancelled_count,
            "q": q,
            "status_filter": status_filter,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


@login_required
def metrics(request):
    if not _require_admin(request, module="system_metrics"):
        return render(request, "core/forbidden.html", status=403)
    data = _compute_metrics()
    return render(request, "core/metrics.html", {"data": data})


def _compute_metrics():
    from datetime import timedelta

    from apps.accounts.models import User
    from apps.applications.models import Application, ApplicationStatus
    from apps.companies.models import Organization
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

    
    # Applied slots by region and district
    applied_by_region_raw = Application.objects.values('slot__district__region__id', 'slot__district__region__name').annotate(count=Count('id')).order_by('-count')
    applied_by_region = [
        {'id': str(r['slot__district__region__id']), 'name': r['slot__district__region__name'] or 'Unknown', 'count': r['count']}
        for r in applied_by_region_raw
    ]

    applied_by_district_raw = Application.objects.values('slot__district__id', 'slot__district__name', 'slot__district__region__id').annotate(count=Count('id')).order_by('-count')
    applied_by_district = [
        {'id': str(r['slot__district__id']), 'name': r['slot__district__name'] or 'Unknown', 'region_id': str(r['slot__district__region__id']), 'count': r['count']}
        for r in applied_by_district_raw
    ]

    return {
        "applied_by_region": applied_by_region,
        "applied_by_district": applied_by_district,
        "users": User.objects.count(),
        "students": StudentProfile.objects.count(),
        "students_verified": StudentProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED
        ).count(),
        "organizations": Organization.objects.count(),
        "organizations_approved": Organization.objects.filter(
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
    from apps.companies.models import Organization
    from apps.students.models import StudentProfile, VerificationStatus
    from apps.accounts.models import User, UserRole
    from django.utils import timezone

    # Students who have registered but have not updated their profiles
    students = (
        User.objects.filter(role=UserRole.STUDENT, student_profile__isnull=True)
        .order_by("-created_at")
    )
    organizations = (
        Organization.objects.filter(verification_status=VerificationStatus.PENDING)
        .exclude(industry="")  # registered-but-unfilled stubs are not reviewable yet
        .select_related("user", "region")
        .order_by("-created_at")
    )
    total_pending = students.count() + organizations.count()
    today = timezone.localdate()
    verified_today = (
        StudentProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED, reviewed_at__date=today
        ).count()
        + Organization.objects.filter(
            verification_status=VerificationStatus.APPROVED, reviewed_at__date=today
        ).count()
    )
    return {
        "students": students,
        "organizations": organizations,
        "total_pending": total_pending,
        "verified_today": verified_today,
        "student_pending": students.count(),
        "organization_pending": organizations.count(),
    }

@login_required
def verification_queue(request):
    if not _require_admin(request, module="user_management"):
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


def _missing_docs(owner_user, profile_kind, organization=None):
    from apps.documents.models import Document

    if organization is not None:
        qs = Document.objects.filter(
            owner_id__in=organization.historical_member_user_ids(),
            doc_type__in=_required_docs(profile_kind),
        )
    else:
        qs = Document.objects.filter(
            owner=owner_user, doc_type__in=_required_docs(profile_kind)
        )
    have = set(qs.values_list("doc_type", flat=True))
    return [d for d in _required_docs(profile_kind) if d not in have]


@login_required
def student_verification(request, pk):
    from apps.documents.models import Document
    from apps.students.models import StudentProfile, VerificationStatus

    if not _require_admin(request, module="user_management"):
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
        elif decision == "deactivate":
            student.user.is_active = False
            student.user.save(update_fields=["is_active"])
            _write_audit(request, "Account Deactivated", "Verification",
                         f"Deactivated student account {student.user.email} (soft delete).")
            messages.success(request, "Student account has been deactivated.")
            return redirect("platform-directory")
    return render(
        request,
        "core/student_verification.html",
        {"student": student, "docs": docs, "missing_docs": _missing_docs(student.user, "student")},
    )


@login_required
def organization_verification(request, pk):
    from apps.accounts.models import User
    from apps.companies.models import Organization
    from apps.documents.models import Document
    from apps.students.models import VerificationStatus

    if not _require_admin(request, module="user_management"):
        return render(request, "core/forbidden.html", status=403)
    organization = get_object_or_404(Organization, pk=pk)
    docs = Document.objects.filter(owner_id__in=organization.historical_member_user_ids()).select_related("owner")
    if request.method == "POST":
        decision = request.POST.get("decision")
        reason = request.POST.get("reason", "").strip()
        if decision == "approve":
            missing = _missing_docs(organization.user, "organization", organization=organization)
            if missing:
                messages.error(request, "Missing required documents — cannot approve.")
            else:
                organization.verification_status = VerificationStatus.APPROVED
                organization.rejection_reason = ""
                organization.reviewed_by = request.user
                organization.reviewed_at = timezone.now()
                organization.save(
                    update_fields=[
                        "verification_status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                _write_audit(request, "Organization Approved", "Verification",
                             f"Approved organization {organization.name} ({organization.industry}).")
                messages.success(request, "Organization profile approved.")
                return redirect("platform-verifications")
        elif decision == "reject":
            if not reason:
                messages.error(request, "A rejection reason is required.")
            else:
                organization.verification_status = VerificationStatus.REJECTED
                organization.rejection_reason = reason
                organization.reviewed_by = request.user
                organization.reviewed_at = timezone.now()
                organization.save(
                    update_fields=[
                        "verification_status",
                        "rejection_reason",
                        "reviewed_by",
                        "reviewed_at",
                        "updated_at",
                    ]
                )
                _write_audit(request, "Organization Rejected", "Verification",
                             f"Rejected organization {organization.name}: {reason}")
                messages.success(request, "Organization profile rejected.")
                return redirect("platform-verifications")
        elif decision == "deactivate":
            User.objects.filter(id__in=organization.member_user_ids()).update(is_active=False)
            _write_audit(request, "Account Deactivated", "Verification",
                         f"Deactivated organization account {organization.name} (soft delete).")
            messages.success(request, "Organization account has been deactivated.")
            return redirect("platform-directory")
    return render(
        request,
        "core/organization_verification.html",
        {"organization": organization, "docs": docs, "missing_docs": _missing_docs(organization.user, "organization", organization=organization)},
    )


@login_required
def platform_student_edit(request, pk):
    from apps.students.models import StudentProfile
    from apps.students.forms import StudentProfileForm
    from apps.core.cache import get_regions
    import json
    from django.urls import reverse

    if not _require_admin(request, module="user_management", cap="edit"):
        return render(request, "core/forbidden.html", status=403)
    student = get_object_or_404(StudentProfile, pk=pk)
    
    if request.method == "POST":
        form = StudentProfileForm(
            request.POST, request.FILES, instance=student, user=student.user, require_phone=False
        )
        if form.is_valid():
            form.save()
            _write_audit(request, "Student Profile Edited", "Admin Edit", f"Edited student {student.user.email}")
            messages.success(request, "Student profile updated successfully.")
            return redirect("platform-student-verification", pk=student.pk)
    else:
        form = StudentProfileForm(instance=student, user=student.user, require_phone=False)
        
    return render(request, "core/admin_profile_edit.html", {
        "form": form,
        "profile": student,
        "profile_type": "Student",
        "cancel_url": reverse("platform-student-verification", args=[student.pk]),
        "regions_json": safe_json_dumps(get_regions()),
    })


@login_required
def platform_organization_edit(request, pk):
    from apps.companies.models import Organization
    from apps.companies.forms import OrganizationProfileForm
    from apps.core.cache import get_regions
    from django.urls import reverse

    if not _require_admin(request, module="user_management", cap="edit"):
        return render(request, "core/forbidden.html", status=403)
    organization = get_object_or_404(Organization, pk=pk)
    
    if request.method == "POST":
        form = OrganizationProfileForm(request.POST, request.FILES, instance=organization)
        if form.is_valid():
            form.save()
            _write_audit(request, "Organization Profile Edited", "Admin Edit", f"Edited organization {organization.name}")
            messages.success(request, "Organization profile updated successfully.")
            return redirect("platform-organization-verification", pk=organization.pk)
    else:
        form = OrganizationProfileForm(instance=organization)
        
    return render(request, "core/admin_profile_edit.html", {
        "form": form,
        "profile": organization,
        "profile_type": "Organization",
        "cancel_url": reverse("platform-organization-verification", args=[organization.pk]),
        "regions_json": safe_json_dumps(get_regions()),
    })


@login_required
def notification_templates(request):
    from apps.notifications.models import NotificationTemplate

    if not _require_admin(request, super_only=True):
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
    """Platform audit console: every organization's activity, with shared filters."""
    from urllib.parse import urlencode

    from django.http import HttpResponse

    from apps.companies.models import Organization
    from apps.core import audit as audit_lib
    from apps.core.pagination import paginate

    if not _require_admin(request, module="system_metrics"):
        return render(request, "core/forbidden.html", status=403)
    params = request.GET
    logs = audit_lib.filter_audit_logs(
        AuditLog.objects.all().select_related("actor", "organization"), params
    ).order_by("-created_at")
    if audit_lib.has_filters(params):
        audit_lib.audit_event(
            request,
            action="Audit Log Searched",
            module="Audit",
            description="Searched the platform audit log",
            context={k: v for k, v in params.items() if k in audit_lib.FILTER_KEYS and v},
        )
    if params.get("export") == "csv":
        audit_lib.audit_event(
            request,
            action="Audit Log Exported",
            module="Audit",
            description="Exported the platform audit log (CSV)",
            context={k: v for k, v in params.items() if k in audit_lib.FILTER_KEYS and v},
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="platform-audit.csv"'
        return audit_lib.write_csv(response, logs)
    modules = list(AuditLog.objects.values_list("module", flat=True).distinct().order_by("module"))
    actions = list(AuditLog.objects.values_list("action", flat=True).distinct().order_by("action"))
    page_obj = paginate(logs, params.get("page"), page_size=50)
    return render(
        request,
        "core/audit_logs.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "querystring": urlencode({k: v for k, v in params.items() if k != "page"}),
            "modules": modules,
            "actions": actions,
            "organizations": Organization.objects.order_by("name").values("id", "name"),
            "f": params,
            "total": AuditLog.objects.count(),
        },
    )


@login_required
def audit_detail(request, log_id):
    if not _require_admin(request, module="system_metrics"):
        return render(request, "core/forbidden.html", status=403)
    log = get_object_or_404(AuditLog.objects.select_related("actor", "organization"), id=log_id)
    return render(
        request,
        "core/audit_detail.html",
        {"log": log, "back_url": "platform-audit-logs", "scope": "platform"},
    )


@login_required
@hardware_reverify
def role_management(request):
    from apps.accounts.models import AdminRole

    if not _require_admin(request, super_only=True):
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
@hardware_reverify
def admin_settings(request):
    if not _require_admin(request, super_only=True):
        return render(request, "core/forbidden.html", status=403)
    if request.method == "POST":
        updates = {
            "app_fee_amount": (request.POST.get("app_fee_amount"), "number"),
            "max_applications_per_student": (request.POST.get("max_applications_per_student"), "number"),
            "default_slot_limit": (request.POST.get("default_slot_limit"), "number"),
            "sms_enabled": (request.POST.get("sms_enabled") == "on", "bool"),
            "sms_api_endpoint": (request.POST.get("sms_api_endpoint"), "text"),
            "maintenance_mode": (request.POST.get("maintenance_mode") == "on", "bool"),
            "student_req_student_id": (request.POST.get("student_req_student_id") == "on", "bool"),
            "student_req_transcript": (request.POST.get("student_req_transcript") == "on", "bool"),
            "student_req_cv": (request.POST.get("student_req_cv") == "on", "bool"),
        }
        new_sms_key = (request.POST.get("sms_api_key") or "").strip()
        if new_sms_key:
            updates["sms_api_key"] = (new_sms_key, "text")

        parsed_numbers = {}
        for key, (value, vtype) in updates.items():
            if vtype == "number":
                if value in (None, ""):
                    parsed_numbers[key] = 0.0
                else:
                    try:
                        num = float(value)
                    except (TypeError, ValueError):
                        messages.error(
                            request,
                            f"Invalid number for '{key.replace('_', ' ')}'. Please enter a valid numeric value.",
                        )
                        return redirect("platform-settings")
                    if not math.isfinite(num) or num < 0:
                        messages.error(
                            request,
                            f"Value for '{key.replace('_', ' ')}' must be a non-negative number.",
                        )
                        return redirect("platform-settings")
                    parsed_numbers[key] = num

        for key, (value, vtype) in updates.items():
            setting, _ = PlatformSetting.objects.get_or_create(
                key=key,
                defaults={
                    "label": key.replace("_", " ").title(),
                    "value_type": vtype,
                    "value_text": "",
                    "value_number": parsed_numbers.get(key) if vtype == "number" else None,
                    "value_bool": False,
                },
            )
            if vtype == "number":
                setting.value_number = parsed_numbers[key]
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
    """Admin directory of all students and organizations with filters."""
    from apps.companies.models import Organization
    from apps.locations.models import District, Region, Ward
    from django.db.models import Q
    from apps.students.models import StudentProfile, VerificationStatus

    if not _require_admin(request, module="user_management"):
        return render(request, "core/forbidden.html", status=403)

    tab = request.GET.get("tab", "students")
    region_id = request.GET.get("region") or None
    district_id = request.GET.get("district") or None
    ward_id = request.GET.get("ward") or None
    university = (request.GET.get("university") or "").strip()
    course = (request.GET.get("course") or "").strip()
    year = request.GET.get("year") or None
    education_level = request.GET.get("education_level") or None
    academic_year = request.GET.get("academic_year") or None
    status = request.GET.get("status") or None
    account = "suspended" if request.GET.get("account") == "suspended" else ""

    if tab == "organizations":
        if account:
            qs = Organization.objects.filter(suspended_at__isnull=False)
        else:
            qs = Organization.objects.filter(
                Q(user__is_active=True) | Q(members__user__is_active=True)
            )
        qs = (
            qs.distinct()
            .select_related("user", "region", "district", "ward")
            .order_by("-created_at")
        )
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
        qs = (
            StudentProfile.objects.filter(user__suspended_at__isnull=False)
            if account
            else StudentProfile.objects.filter(user__is_active=True)
        ).select_related("user", "university", "region", "district", "ward").order_by("-created_at")
        if region_id:
            qs = qs.filter(region_id=region_id)
        if district_id:
            qs = qs.filter(district_id=district_id)
        if ward_id:
            qs = qs.filter(ward_id=ward_id)
        if university:
            qs = qs.filter(
                Q(university__name__icontains=university)
                | Q(university__abbreviation__icontains=university)
            )
        if course:
            qs = qs.filter(course__icontains=course)
        if year:
            qs = qs.filter(academic_year=year)
        if education_level:
            qs = qs.filter(education_level=education_level)
            if education_level == "7" and academic_year:
                qs = qs.filter(academic_year=academic_year)
        if status:
            qs = qs.filter(verification_status=status)
        rows = qs

    regions = [{"id": str(r["id"]), "name": r["name"]} for r in Region.objects.order_by("name").values("id", "name")]
    districts = (
        [{"id": str(d["id"]), "name": d["name"]} for d in District.objects.filter(region_id=region_id).order_by("name").values("id", "name")]
        if region_id
        else []
    )
    wards = (
        [{"id": str(w["id"]), "name": w["name"]} for w in Ward.objects.filter(district_id=district_id).order_by("name").values("id", "name")]
        if district_id
        else []
    )
    years = [i for i in range(1, 9)]
    statuses = VerificationStatus.choices
    from apps.core.education import education_level_choices, academic_year_choices
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
            "regions_json": safe_json_dumps(regions),
            "institutions_json": safe_json_dumps(institutions),
            "years": years,
            "statuses": statuses,
            "education_levels": education_level_choices(),
            "academic_years": academic_year_choices(),
            "filters": {
                "region": region_id,
                "district": district_id,
                "ward": ward_id,
                "university": university,
                "course": course,
                "year": year or "",
                "education_level": education_level or "",
                "academic_year": academic_year or "",
                "status": status or "",
                "account": account,
            },
        },
    )


@login_required
def integrity(request):
    """Enterprise integrity dashboard — verifies the immutable ledger chain."""
    if not _require_admin(request, module="system_metrics"):
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
            IntegrityRecord.objects.filter(record_type=rtype).order_by("created_at", "id")        )
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

@login_required
def admin_slots(request):
    """Admin view for browsing slots without applying."""
    from apps.slots.models import Slot
    from apps.core.pagination import paginate
    from urllib.parse import urlencode
    from apps.core.cache import get_regions
    from apps.core.education import education_level_choices, academic_year_choices
    from django.db.models import Q

    if not _require_admin(request, module="slot_verification"):
        return render(request, "core/forbidden.html", status=403)

    qs = request.GET.get("q", "").strip()
    region_id = request.GET.get("region", "")
    district_id = request.GET.get("district", "")
    department = request.GET.get("department", "").strip()
    education_level = request.GET.get("education_level", "")
    academic_year = request.GET.get("academic_year", "")

    slots = Slot.objects.all().select_related("organization__user", "district__region")
    
    if qs:
        slots = slots.filter(
            Q(title__icontains=qs) | 
            Q(organization__name__icontains=qs) | 
            Q(description__icontains=qs)
        )
    if region_id:
        slots = slots.filter(district__region_id=region_id)
    if district_id:
        slots = slots.filter(district_id=district_id)
    if department:
        slots = slots.filter(department__icontains=department)
    if education_level:
        slots = slots.filter(education_level=education_level)
    if academic_year:
        slots = slots.filter(academic_year=academic_year)
        
    slots = slots.order_by("-created_at")

    page_obj = paginate(slots, request.GET.get("page"), page_size=20)
    
    return render(
        request, 
        "core/admin_slots.html", 
        {
            "slots": page_obj, 
            "page_obj": page_obj, 
            "q": qs,
            "region": region_id,
            "district": district_id,
            "department": department,
            "education_level": education_level,
            "academic_year": academic_year,
            "regions": get_regions(),
            "education_levels": education_level_choices(),
            "academic_years": academic_year_choices(),
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
        }
    )

    if not _require_admin(request, module="slot_verification"):
        return render(request, "core/forbidden.html", status=403)

    qs = request.GET.get("q", "").strip()
    slots = Slot.objects.all().select_related("organization__user", "district__region")
    
    if qs:
        from django.db.models import Q
        slots = slots.filter(
            Q(title__icontains=qs) | 
            Q(organization__name__icontains=qs) | 
            Q(description__icontains=qs)
        )
        
    slots = slots.order_by("-created_at")

    page_obj = paginate(slots, request.GET.get("page"), page_size=20)
    
    return render(
        request, 
        "core/admin_slots.html", 
        {
            "slots": page_obj, 
            "page_obj": page_obj, 
            "q": qs,
            "querystring": urlencode({k: v for k, v in request.GET.items() if k != "page"}),
        }
    )
